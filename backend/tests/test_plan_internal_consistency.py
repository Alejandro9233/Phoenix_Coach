"""Regression tests for the 2026-09-05 internally contradictory long run.

The LLM shipped "Marathon Pace Long Run" declaring 16 km / 118 min while its
own steps described 23 km ("3 km easy" + "13 km" + "5 km" + "2 km"), with a
4:00/km "easy jog" cooldown. Every gate hole it slipped through is locked
here:

- declared distance_km vs the steps' sum: >15% disagreement is an internal
  contradiction — a HARD, unrepairable gate failure. The pipeline raises
  (502, nothing persists) instead of rewriting the numbers.
- step-text km are SUMMED per step ("2x1 km" = 2), never largest-wins.
- every step with a km figure and a duration gets a zone-plausibility pace
  check, both directions (the 4:00/km cooldown), loose on purpose.
- the long-run progression check has an overshoot direction (>25% over the
  engine target is hard) and runs on windowed replans too.
- adaptation preserves distance_km — adapt_daily's output format omits it.
"""
import copy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.main as main_mod
from backend.agents.response_agent import ResponseAgent
from backend.main import adapt_today_workout, app, get_db
from backend.models.database import Athlete, Base, RecoverySnapshot, WeeklyPlan
from backend.services.pace_enforcer import audit_step_paces
from backend.services.pace_model import compute_pace_model
from backend.services.volume_gate import (
    audit_plan,
    internal_contradiction,
    steps_sum_km,
    workout_km,
)
from backend.utils.timezone import get_local_today

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

PM = compute_pace_model(4.5, "3:10:00", "Marathon")

# The incident workout, verbatim shape: steps sum to 23 km, declares 16.
INCIDENT_WORKOUT = {
    "sport": "running",
    "title": "Marathon Pace Long Run",
    "total_time": "118 min",
    "distance_km": 16.0,
    "steps": [
        {"type": "warmup", "duration": "15:00", "zone": 1,
         "description": "3 km easy"},
        {"type": "main", "duration": "70:00", "zone": 2,
         "description": "13 km at Long-run pace"},
        {"type": "main", "duration": "25:00", "zone": 4,
         "description": "5 km at Marathon pace"},
        {"type": "cooldown", "duration": "08:00", "zone": 1,
         "description": "2 km easy jog"},
    ],
}

AVAIL = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}


def _ctx(**vt):
    return {
        "volume_targets": {"run_km_target": 41.8, "run_km_hard_cap": 44.5,
                           **vt},
        "volume_references": {"phase_hours_range": "10-12",
                              "max_quality_sessions": 2,
                              "sport_sessions": {"running": {"sessions": 4}}},
    }


def _day(workouts):
    return {"summary": "s", "rationale": "r", "coach_note": "c",
            "workouts": workouts}


# ─── (a) the incident workout is rejected ───────────────────────────────────


def test_incident_workout_is_internally_contradictory():
    c = internal_contradiction(INCIDENT_WORKOUT)
    assert c == {"declared_km": 16.0, "steps_km": 23.0}
    # The sums count the honest (larger) figure until the gate kills it.
    assert workout_km(INCIDENT_WORKOUT) == (23.0, "contradictory")


def test_incident_workout_hard_fails_the_audit():
    plan = {"days": {"Saturday": _day([copy.deepcopy(INCIDENT_WORKOUT)])}}
    report = audit_plan(plan, _ctx(), availability=AVAIL)
    v = next(v for v in report.hard if v["kind"] == "internal_contradiction")
    assert v["day"] == "Saturday"
    assert "16 km" in v["detail"] and "23 km" in v["detail"]
    assert not report.ok


def test_generated_week_with_incident_workout_502s_and_persists_nothing(
        client, test_db_session, monkeypatch):
    week = {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {d: _day([{"sport": "rest", "title": "Rest", "steps": [],
                           "total_time": "0:00"}]) for d in DAYS},
    }
    week["days"]["Saturday"] = _day([copy.deepcopy(INCIDENT_WORKOUT)])
    calls = []

    def fake(self, *args, **kwargs):
        calls.append(kwargs.get("feedback"))
        return copy.deepcopy(week)

    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", fake)

    response = client.get("/weekly-plan")

    assert response.status_code == 502, response.text
    assert "no plan persisted" in response.json()["detail"]
    assert len(calls) == 2  # the contradiction earned the one retry
    assert "23 km" in calls[1]
    week_start = get_local_today() - timedelta(days=get_local_today().weekday())
    assert test_db_session.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == week_start).first() is None


# ─── (b) step km are summed, never largest-wins ─────────────────────────────


def test_steps_sum_counts_intervals_and_ignores_non_distances():
    wednesday = {
        "sport": "running", "title": "Cruise Intervals",
        "total_time": "35 min",
        "steps": [
            {"type": "warmup", "duration": "10:00", "zone": 1,
             "description": "2 km easy warmup"},
            {"type": "main", "duration": "10:00", "zone": 4,
             "description": "2x1 km at threshold, 2 min jog between"},
            {"type": "cooldown", "duration": "06:00", "zone": 1,
             "description": "1 km jog"},
        ],
    }
    total, complete = steps_sum_km(wednesday)
    assert total == 5.0  # 2 + 2x1 + 1 — not the old max() of 2
    assert complete
    assert workout_km(wednesday) == (5.0, "parsed")


def test_time_only_steps_leave_declared_alone():
    # Steps that name only part of the distance are a lower bound, not a
    # contradiction — "6 km relaxed" main with time-only warmup/cooldown.
    w = {"sport": "running", "title": "Easy Run", "total_time": "55 min",
         "distance_km": 8.0,
         "steps": [
             {"type": "warmup", "duration": "10:00", "zone": 1,
              "description": "jog"},
             {"type": "main", "duration": "35:00", "zone": 2,
              "description": "6 km relaxed"},
             {"type": "cooldown", "duration": "10:00", "zone": 1,
              "description": "jog"},
         ]}
    assert internal_contradiction(w) is None
    assert workout_km(w) == (8.0, "declared")


# ─── (c) the 4:00/km cooldown is rejected on its own ────────────────────────


def test_impossible_cooldown_pace_is_hard():
    plan = {"days": {"Saturday": _day([{
        "sport": "running", "title": "Easy Run", "total_time": "40 min",
        "distance_km": 2.0,
        "steps": [{"type": "cooldown", "duration": "8:00", "zone": 1,
                   "description": "2 km easy jog"}],
    }])}}
    violations = audit_step_paces(plan, PM)
    v = next(v for v in violations if v["kind"] == "step_pace_implausible")
    assert v["day"] == "Saturday"
    assert "4:00/km" in v["detail"]


def test_incident_cooldown_flagged_inside_the_full_workout():
    plan = {"days": {"Saturday": _day([copy.deepcopy(INCIDENT_WORKOUT)])}}
    violations = audit_step_paces(plan, PM)
    assert any("2 km easy jog" in v["detail"] for v in violations)


# ─── (d) a clean workout passes everything ──────────────────────────────────


def test_clean_workout_passes():
    monday = {
        "sport": "running", "title": "Easy Run", "total_time": "39 min",
        "distance_km": 6.0,
        "steps": [{"type": "main", "duration": "39:00", "zone": 2,
                   "description": "6 km relaxed"}],
    }
    assert internal_contradiction(monday) is None
    assert workout_km(monday) == (6.0, "declared")
    plan = {"days": {"Monday": _day([monday])}}
    assert audit_step_paces(plan, PM) == []
    report = audit_plan(plan, _ctx(long_run_minutes=86), availability=AVAIL)
    assert report.ok


# ─── review false-positive fixes: benchmark text is not distance ────────────


def test_race_pace_vocabulary_is_not_distance():
    # "at 5K pace" names a benchmark, not 5 km of running.
    w = {"sport": "running", "title": "VO2max Intervals",
         "total_time": "35 min", "distance_km": 6.0,
         "steps": [
             {"type": "warmup", "duration": "12:00", "zone": 1,
              "description": "2 km easy warmup"},
             {"type": "main", "duration": "13:30", "zone": 4,
              "description": "3 km at 5K pace"},
             {"type": "cooldown", "duration": "7:00", "zone": 1,
              "description": "1 km jog"},
         ]}
    assert steps_sum_km(w) == (6.0, True)
    assert internal_contradiction(w) is None
    # Metre reps with a pace reference carry no km figure at all.
    reps = {"sport": "running", "title": "VO2max Intervals",
            "distance_km": 7.0, "total_time": "40 min",
            "steps": [{"type": "main", "duration": "20:00", "zone": 4,
                       "description": "6 x 400m at 5k pace, 400m jog"}]}
    assert steps_sum_km(reps) == (None, False)
    assert internal_contradiction(reps) is None
    # "20 min at 10k pace" is time-only, and "10 km pace" never reads 10 km.
    tempo = {"sport": "running", "title": "Tempo Run", "distance_km": 8.0,
             "total_time": "45 min",
             "steps": [{"type": "main", "duration": "20:00", "zone": 3,
                        "description": "20 min at 10k pace"}]}
    assert internal_contradiction(tempo) is None


def test_step_total_beats_interval_notation():
    # A long run that states its own total plus pickups is the total, not
    # the pickups — "14 km with 6 x 1 km" undercounting to 6 was a 502 on a
    # textbook marathon-block session.
    w = {"sport": "running", "title": "Marathon Pace Long Run",
         "total_time": "80 min", "distance_km": 14.0,
         "steps": [{"type": "main", "duration": "80:00", "zone": 2,
                    "description": "14 km with 6 x 1 km at marathon pace"}]}
    assert steps_sum_km(w) == (14.0, True)
    assert internal_contradiction(w) is None


def test_segment_reference_never_proves_completeness():
    # Real historical shape (test_freshness_and_quality_fixes): the only
    # step names a segment — 3 km can't cover 90 min, so the sum stays a
    # lower bound and the below-declared branch must not fire.
    w = {"sport": "running", "title": "Long Run", "total_time": "90 min",
         "distance_km": 16.0,
         "steps": [{"type": "main", "duration": "90:00", "zone": 2,
                    "description": "steady, last 3 km at marathon pace"}]}
    assert steps_sum_km(w) == (3.0, False)
    assert internal_contradiction(w) is None


def test_below_declared_fires_when_every_step_covers():
    # Declared 12 over steps that fully account for 10.0: the LLM padded
    # distance_km. Still a contradiction.
    w = {"sport": "running", "title": "Easy Run", "total_time": "60 min",
         "distance_km": 12.0,
         "steps": [
             {"type": "warmup", "duration": "12:00", "zone": 1,
              "description": "2 km easy"},
             {"type": "main", "duration": "48:00", "zone": 2,
              "description": "8 km steady"},
         ]}
    assert internal_contradiction(w) == {"declared_km": 12.0,
                                         "steps_km": 10.0}


def test_speed_text_and_non_running_are_exempt():
    # "28-30 km/h" is a speed; rides never trip the run-integrity check.
    ride = {"sport": "cycling", "title": "Endurance Ride",
            "total_time": "90 min", "distance_km": 25.0,
            "steps": [{"type": "main", "duration": "90:00", "zone": 2,
                       "description": "steady at 28-30 km/h"}]}
    assert internal_contradiction(ride) is None


def test_decimal_rep_counts_are_not_reps():
    # "1.5x2 km" must not read as 5 reps of 2 km.
    w = {"sport": "running", "title": "Easy Run",
         "steps": [{"type": "main", "duration": "12:00", "zone": 2,
                    "description": "1.5x2 km"}]}
    assert steps_sum_km(w)[0] == 2.0


def test_minutes_style_duration_is_audited():
    # The normalizer emits "8 min" for numeric durations; colon-only
    # parsing let those steps skip the pace audit entirely.
    plan = {"days": {"Sunday": _day([{
        "sport": "running", "title": "Easy Run", "total_time": "40 min",
        "distance_km": 2.0,
        "steps": [{"type": "cooldown", "duration": "8 min", "zone": 1,
                   "description": "2 km easy jog"}],
    }])}}
    violations = audit_step_paces(plan, PM)
    assert any(v["kind"] == "step_pace_implausible" for v in violations)


def test_healable_main_step_is_not_a_violation():
    # _reconcile_main_step exists to fix exactly this shape ("30:00" for
    # "8 km at tempo pace") and enforce_paces applies it to what persists —
    # the audit must not 502 what the pipeline will heal. The cooldown's
    # nonsense still flags, and the audit never mutates the plan.
    pm = compute_pace_model(4.65, "3:10:00", "Marathon")
    plan = {"days": {"Tuesday": _day([{
        "sport": "running", "title": "Tempo Run", "total_time": "50 min",
        "distance_km": 10.0,
        "steps": [
            {"type": "main", "duration": "30:00", "zone": 3,
             "description": "8 km at tempo pace"},
            {"type": "cooldown", "duration": "8:00", "zone": 1,
             "description": "2 km easy jog"},
        ],
    }])}}
    before = copy.deepcopy(plan)
    violations = audit_step_paces(plan, pm)
    assert plan == before
    assert all("8 km at tempo pace" not in v["detail"] for v in violations)
    assert any("2 km easy jog" in v["detail"] for v in violations)


# ─── shared endpoint fixtures ───────────────────────────────────────────────


@pytest.fixture
def test_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    session.add(Athlete(
        name="Test Athlete",
        race_date=get_local_today() + timedelta(weeks=10),
        race_distance="Marathon",
        swim_days="wed,sat,sun",
        bike_days="mon,tue,wed,thu,fri,sat,sun",
        run_days="mon,tue,wed,thu,fri,sat,sun",
        strength_days="mon,wed,fri",
    ))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(test_db_session):
    def override_get_db():
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_week(session):
    week = {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {d: _day([{"sport": "running", "title": "Easy Run",
                           "total_time": "30 min", "distance_km": 5.0,
                           "steps": []}]) for d in DAYS},
    }
    week_start = get_local_today() - timedelta(days=get_local_today().weekday())
    session.add(WeeklyPlan(week_start=week_start, athlete_id=1, plan_json=week))
    session.commit()


# ─── (e) replan_remaining hits the long-run overshoot ───────────────────────


def test_replan_long_run_overshoot_502s_and_keeps_the_plan(
        client, test_db_session, monkeypatch):
    _seed_week(test_db_session)
    today_name = get_local_today().strftime("%A")
    # 118 min against the engine's 70-min no-history target: +69%. Distance
    # kept consistent so ONLY the overshoot fires.
    monster = {"days": {today_name: _day([{
        "sport": "running", "title": "Long Run", "total_time": "118 min",
        "distance_km": 19.5, "steps": []}])}}
    calls = []

    def fake(self, *args, **kwargs):
        calls.append(kwargs.get("feedback"))
        return copy.deepcopy(monster)

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", fake)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "m")

    response = client.post("/weekly-plan/replan-remaining")

    assert response.status_code == 502, response.text
    assert "left untouched" in response.json()["detail"]
    assert len(calls) == 2
    assert "longest run is 118" in calls[1]

    week_start = get_local_today() - timedelta(days=get_local_today().weekday())
    row = test_db_session.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == week_start).order_by(
        WeeklyPlan.id.desc()).first()
    stored = row.plan_json["days"][today_name]["workouts"][0]
    assert stored["total_time"] == "30 min"  # the seeded plan survived


def test_replan_modest_long_run_passes(client, test_db_session, monkeypatch):
    _seed_week(test_db_session)
    today_name = get_local_today().strftime("%A")
    # A few minutes over the 70-min target must NOT fail.
    fine = {"days": {today_name: _day([{
        "sport": "running", "title": "Long Run", "total_time": "75 min",
        "distance_km": 12.5, "steps": []}])}}
    monkeypatch.setattr(ResponseAgent, "generate_remaining_days",
                        lambda self, *a, **kw: copy.deepcopy(fine))
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "m")

    response = client.post("/weekly-plan/replan-remaining")
    assert response.status_code == 200, response.text


# ─── (f) adaptation preserves distance_km ───────────────────────────────────


def _seed_adapt(db):
    _seed_week(db)
    db.add(RecoverySnapshot(date=get_local_today(), athlete_id=1, hrv_ms=86.0,
                            resting_hr=51, fatigue_state=2, load_ratio=1.0,
                            tib=0.0))
    db.commit()


def test_adaptation_preserves_distance_km(test_db_session, monkeypatch):
    _seed_adapt(test_db_session)

    def _adapt(self, day, metrics, training_context=None):
        # adapt_daily's real output format: trimmed session, no distance_km.
        return {"summary": "adapted", "rationale": "r", "coach_note": "c",
                "adaptation": "trimmed",
                "workouts": [{"sport": "running", "title": "Easy Run",
                              "total_time": "18 min", "steps": []}]}

    monkeypatch.setattr(
        "backend.agents.response_agent.ResponseAgent.adapt_daily", _adapt)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "metrics")

    adapted = adapt_today_workout(body=None, db=test_db_session)
    w = adapted["workouts"][0]
    # Original: 5.0 km in 30 min; trimmed to 18 min -> scaled, never absent.
    assert w["distance_km"] == 3.0


def test_adaptation_recomputes_distance_from_steps(test_db_session, monkeypatch):
    _seed_adapt(test_db_session)

    def _adapt(self, day, metrics, training_context=None):
        return {"summary": "adapted", "rationale": "r", "coach_note": "c",
                "adaptation": "swapped",
                "workouts": [{"sport": "running", "title": "Easy Run",
                              "total_time": "25 min",
                              "steps": [{"type": "main", "duration": "25:00",
                                         "zone": 2,
                                         "description": "4 km very easy"}]}]}

    monkeypatch.setattr(
        "backend.agents.response_agent.ResponseAgent.adapt_daily", _adapt)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "metrics")

    adapted = adapt_today_workout(body=None, db=test_db_session)
    assert adapted["workouts"][0]["distance_km"] == 4.0


def test_adaptation_never_overwrites_deliberate_distance():
    original = _day([{"sport": "running", "title": "Easy Run",
                      "total_time": "30 min", "distance_km": 5.0,
                      "steps": []}])
    adapted = _day([{"sport": "running", "title": "Easy Run",
                     "total_time": "20 min", "distance_km": 4.2,
                     "steps": []}])
    main_mod._preserve_adapted_distances(adapted, original)
    assert adapted["workouts"][0]["distance_km"] == 4.2


def test_adaptation_pairs_same_sport_workouts_in_order():
    # Two runs on one day: each adapted session inherits its own original's
    # distance, not the first original's twice.
    original = _day([
        {"sport": "running", "title": "Easy Run", "total_time": "30 min",
         "distance_km": 5.0, "steps": []},
        {"sport": "running", "title": "Shakeout", "total_time": "20 min",
         "distance_km": 3.0, "steps": []},
    ])
    adapted = _day([
        {"sport": "running", "title": "Easy Run", "total_time": "30 min",
         "steps": []},
        {"sport": "running", "title": "Shakeout", "total_time": "20 min",
         "steps": []},
    ])
    main_mod._preserve_adapted_distances(adapted, original)
    assert [w["distance_km"] for w in adapted["workouts"]] == [5.0, 3.0]
