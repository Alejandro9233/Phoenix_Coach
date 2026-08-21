"""B1/B4: the volume gate — planned volume is measured, graded, and bounded.

Rules locked in here:
- workout_km tiers: declared distance_km beats parsed text beats estimated
  minutes/6; an implausible declared pace falls through.
- Over-ceiling is HARD (retry, then strip whole sessions — never the week's
  longest run); floors are SOFT (retry, then persist with gate_warnings).
  The gate never turns a generated week into a 502.
- Completed actuals credit the band: a replan after 20 km already run only
  needs the remainder.
- An active running injury or zero open run days disables the floor — an
  injured or travel-compressed week legitimately under-runs.
- B4: apply_travel measures displaced run km before resting the blocked
  days, requires the rebuild to preserve it (feasibility-capped), and
  always reports {before, after, required, guaranteed}.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import ResponseAgent
from backend.main import app, get_db
from backend.models.database import Athlete, Base, WeeklyPlan
from backend.services import volume_gate
from backend.services.volume_gate import (
    audit_plan,
    apply_terminal_repairs,
    canonical_title,
    compute_budget,
    is_quality,
    parse_minutes,
    workout_km,
)
from backend.utils.timezone import get_local_today


@pytest.fixture
def test_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    athlete = Athlete(
        name="Test Athlete",
        race_date=get_local_today() + timedelta(weeks=10),  # Marathon build
        race_distance="Marathon",
        swim_days="wed,sat,sun",
        bike_days="mon,tue,wed,thu,fri,sat,sun",
        run_days="mon,tue,wed,thu,fri,sat,sun",
        strength_days="mon,wed,fri",
    )
    session.add(athlete)
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


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _run_workout(km, title="Easy Run"):
    return {
        "sport": "running",
        "title": title,
        "steps": [],
        "total_time": f"{round(km * 5.5)} min",
        "distance_km": km,
    }


def _week(run_km_by_day):
    """A raw plan dict: {"Monday": 8, "Friday": (8, "Tempo Run"), ...} ->
    runs (km or (km, title)); missing days rest."""
    days = {}
    for day in DAYS:
        km = run_km_by_day.get(day)
        if km:
            km, title = km if isinstance(km, tuple) else (km, "Easy Run")
            days[day] = {
                "summary": "run", "rationale": "r", "coach_note": "c",
                "workouts": [_run_workout(km, title)],
            }
        else:
            days[day] = {
                "summary": "rest", "rationale": "r", "coach_note": "c",
                "workouts": [{"sport": "rest", "title": "Rest", "steps": [],
                              "total_time": "0:00"}],
            }
    return {
        "week_summary": {"focus": "f", "rationale": "r",
                         "expected_total_hours": 99.0, "expected_run_km": 99.0},
        "days": days,
    }


# A context shaped like compute_context's output, with C3's targets and the
# Marathon-base menu.
def _ctx(target=40.0, hard_cap=44.0, hours="10-12", quality=2, phase="base",
         recovery_status="green", is_recovery_week=False):
    return {
        "phase": phase,
        "phase_name": f"Marathon {phase.capitalize()}",
        "is_recovery_week": is_recovery_week,
        "recovery": {"status": recovery_status},
        "volume_targets": {"run_km_target": target, "run_km_hard_cap": hard_cap},
        "volume_references": {
            "phase_hours_range": hours,
            "max_quality_sessions": quality,
            "sport_sessions": {"running": {"sessions": 4}},
        },
        "workout_menu": {
            "running": ["Easy Run", "Long Run", "Strides/Openers",
                        "Tempo Run", "Cruise Intervals"],
            "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
            "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
        },
        "forbidden_workouts": ["VO2max Intervals", "Marathon Pace Long Run",
                               "Race Simulation Brick", "Long Run (>15 km)"],
    }


AVAIL = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}


def test_parse_minutes_formats():
    assert parse_minutes("45 min") == 45
    assert parse_minutes("45:00") == 45
    assert parse_minutes("1:15:00") == 75
    assert parse_minutes("0:00") == 0
    assert parse_minutes(45) == 45
    assert parse_minutes("garbage") is None


def test_workout_km_tiers():
    declared = {"sport": "running", "title": "Easy Run", "total_time": "60 min",
                "distance_km": 10.0, "steps": []}
    assert workout_km(declared) == (10.0, "declared")

    parsed = {"sport": "running", "title": "Long Run 14 km", "total_time": "90 min",
              "steps": [{"description": "3x2km at tempo"}]}
    km, source = workout_km(parsed)
    assert (km, source) == (14.0, "parsed")  # largest figure wins

    estimated = {"sport": "running", "title": "Easy Run", "total_time": "60 min",
                 "steps": []}
    assert workout_km(estimated) == (10.0, "estimated")

    implausible = {"sport": "running", "title": "Easy Run", "total_time": "40 min",
                   "distance_km": 50.0, "steps": []}
    km, source = workout_km(implausible)  # 0.8 min/km is not a human pace
    assert source == "estimated"

    strength = {"sport": "strength", "title": "Gym", "total_time": "60 min",
                "steps": [], "distance_km": 5.0}
    assert workout_km(strength) == (0.0, "none")


def test_audit_flags_ceiling_hard_and_floor_soft():
    over = _week({"Monday": 12, "Wednesday": 12, "Friday": 12, "Sunday": 24})  # 60
    report = audit_plan(over, _ctx(), availability=AVAIL)
    assert [v["kind"] for v in report.hard] == ["run_km_high"]
    assert not report.ok

    under = _week({"Monday": 5, "Thursday": 5})  # 10 vs floor 36
    report = audit_plan(under, _ctx(), availability=AVAIL)
    assert report.ok  # floors never block
    assert "run_km_low" in [v["kind"] for v in report.soft]

    fits = _week({"Monday": 10, "Wednesday": 10,
                  "Friday": (8, "Tempo Run"), "Sunday": 14})  # 42
    report = audit_plan(fits, _ctx(hours="3-5"), availability=AVAIL)
    assert report.ok and not report.soft


def test_completed_actuals_credit_the_band():
    """Replanning after 20 km already run: 15 km planned + 20 done clears a
    40-target week's floor scaled to the remaining open days."""
    window = ["Friday", "Saturday", "Sunday"]
    plan = _week({"Saturday": 7, "Sunday": 8})
    report = audit_plan(plan, _ctx(), days=window, availability=AVAIL,
                        completed_run_km=20.0, completed_hours=4.0)
    assert report.ok
    # floor = 36, feasibility = 3 open days / 4 sessions -> effective
    # floor = 20 + (36-20)*0.75 = 32; week = 20+15 = 35 >= 32.
    assert not [v for v in report.soft if v["kind"] == "run_km_low"]
    assert report.metrics["run_km_week"] == 35.0


def test_running_injury_disables_the_floor():
    class Injury:
        affected_sports = "run"
        body_part = "calf"
        severity = 5

    under = _week({"Monday": 5})
    report = audit_plan(under, _ctx(), availability=AVAIL,
                        active_injuries=[Injury()])
    assert not [v for v in report.soft if v["kind"] == "run_km_low"]


def test_travel_required_km_is_hard_and_window_scoped():
    plan = _week({"Friday": 4})  # thin rebuild
    report = audit_plan(plan, _ctx(), days=["Friday", "Saturday", "Sunday"],
                        availability=AVAIL, completed_run_km=30.0,
                        required_run_km=15.0)
    kinds = [v["kind"] for v in report.hard]
    # 30 km already run cannot cover a displaced-km shortfall.
    assert "travel_run_km" in kinds


def test_terminal_repair_strips_shortest_never_longest():
    plan = _week({"Monday": 8, "Tuesday": 10, "Wednesday": 8,
                  "Thursday": 10, "Saturday": 8, "Sunday": 16})  # 60
    report = audit_plan(plan, _ctx(), availability=AVAIL)
    assert not report.ok
    plan, repairs = apply_terminal_repairs(plan, report)

    stripped_days = {r["day"] for r in repairs}
    assert "Sunday" not in stripped_days  # the longest run survives
    assert volume_gate.planned_run_km(plan) <= 44 + volume_gate.RUN_CEILING_GRACE_KM
    for day in stripped_days:
        workouts = plan["days"][day]["workouts"]
        assert workouts[0]["sport"] == "rest"
        assert workouts[0]["enforced_reason"]


def test_compute_budget_degrades_gracefully():
    assert compute_budget({}) is None
    partial = compute_budget({"volume_references": {"phase_hours_range": "9-10"}})
    assert partial["hours_low"] == 9.0
    assert partial["run_km_target"] is None


def test_feedback_text_carries_numbers():
    over = _week({"Monday": 12, "Wednesday": 12, "Friday": 12, "Sunday": 24})
    report = audit_plan(over, _ctx(), availability=AVAIL)
    text = report.feedback_text()
    assert "FAILED VALIDATION" in text
    assert "60.0" in text and "44.0" in text
    assert "Fix ONLY these problems" in text


# ─── Endpoint loop: retry with feedback, repair at terminal ─────────────────


def _fake_generator(weeks):
    """Returns (fake_method, calls) — pops one week per call, records feedback."""
    calls = []
    queue = list(weeks)

    def fake(self, summary, profile, training_context=None, feedback=None):
        calls.append(feedback)
        return queue.pop(0) if len(queue) > 1 else dict(queue[0])

    return fake, calls


def _persisted(session):
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    row = (
        session.query(WeeklyPlan)
        .filter(WeeklyPlan.week_start == week_start)
        .order_by(WeeklyPlan.id.desc())
        .first()
    )
    return row.plan_json if row else None


def test_over_ceiling_retries_with_feedback_then_persists_attempt_two(
        client, test_db_session, monkeypatch):
    over = _week({"Monday": 12, "Wednesday": 12, "Friday": 12, "Sunday": 24})  # 60
    fits = _week({"Monday": 10, "Wednesday": 10, "Friday": 8, "Sunday": 14})  # 42
    fake, calls = _fake_generator([over, fits])
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", fake)

    response = client.get("/weekly-plan")
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert calls[0] is None
    assert "60.0" in calls[1] and "FAILED VALIDATION" in calls[1]

    plan = _persisted(test_db_session)
    # Attempt 2 persisted, and the stamped sums are Python's, not the LLM's 99.
    assert plan["week_summary"]["expected_run_km"] == 42.0
    assert plan["week_summary"]["expected_total_hours"] != 99.0


def test_both_attempts_over_ceiling_repairs_not_502(client, test_db_session, monkeypatch):
    over = _week({"Monday": 8, "Tuesday": 10, "Wednesday": 8,
                  "Thursday": 10, "Saturday": 8, "Sunday": 16})  # 60
    fake, calls = _fake_generator([over])
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", fake)

    response = client.get("/weekly-plan")
    assert response.status_code == 200, response.text
    assert len(calls) == 2

    plan = _persisted(test_db_session)
    assert plan["week_summary"]["expected_run_km"] <= 44 + volume_gate.RUN_CEILING_GRACE_KM
    # The longest run survived the trim; a receipt records what was cut.
    sunday = plan["days"]["Sunday"]["workouts"][0]
    assert sunday["sport"] == "running"
    stripped = plan["_revisions"][-1]["stripped"]
    assert any("over budget" in (s.get("reason") or "") for s in stripped)


def test_both_attempts_under_floor_persists_with_warning(client, test_db_session, monkeypatch):
    thin = _week({"Monday": 5, "Thursday": 5})  # 10 km vs target 40
    fake, calls = _fake_generator([thin])
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", fake)

    response = client.get("/weekly-plan")
    assert response.status_code == 200, response.text
    assert len(calls) == 2  # the soft floor still earns the one retry

    plan = _persisted(test_db_session)
    warnings = plan["week_summary"].get("gate_warnings") or []
    assert any("target" in w for w in warnings)
    # No Python-invented sessions: the thin week persisted as generated.
    assert plan["week_summary"]["expected_run_km"] == 10.0


# ─── B2: titles are menu vocabulary, not prompt prose ───────────────────────


def test_canonical_title_exact_match_with_parentheticals():
    menu = ["Easy Run", "Long Run (Z1-Z2 only)", "Tempo Run"]
    assert canonical_title("Long Run", menu) == "Long Run (Z1-Z2 only)"
    assert canonical_title("long run (90 min)", menu) == "Long Run (Z1-Z2 only)"
    assert canonical_title("TEMPO RUN", menu) == "Tempo Run"
    assert canonical_title("Fartlek Surges", menu) is None


def test_forbidden_and_off_menu_titles_are_hard():
    plan = _week({"Monday": 8, "Saturday": (12, "Marathon Pace Long Run")})
    report = audit_plan(plan, _ctx(), availability=AVAIL)
    kinds = [v["kind"] for v in report.hard]
    assert "forbidden_title" in kinds

    plan = _week({"Monday": 8, "Thursday": (10, "VO2max Intervals")})
    report = audit_plan(plan, _ctx(), availability=AVAIL)
    assert any(v["kind"] == "forbidden_title" and "VO2max" in v["title"]
               for v in report.hard)

    plan = _week({"Monday": 8, "Friday": (8, "Tempo Run"), "Sunday": 14})
    report = audit_plan(plan, _ctx(), availability=AVAIL)
    assert not [v for v in report.hard if v["kind"] == "forbidden_title"]


def test_unrecognized_title_hard_only_when_quality():
    hard_unknown = _week({"Monday": 8})
    hard_unknown["days"]["Wednesday"] = {
        "summary": "s", "rationale": "r", "coach_note": "c",
        "workouts": [{"sport": "running", "title": "Fartlek Surges",
                      "total_time": "45 min", "distance_km": 8.0,
                      "steps": [{"type": "main", "zone": 4, "duration": "20:00"}]}],
    }
    report = audit_plan(hard_unknown, _ctx(), availability=AVAIL)
    assert any(v["kind"] == "forbidden_title" for v in report.hard)

    easy_unknown = _week({"Monday": 8})
    easy_unknown["days"]["Wednesday"] = {
        "summary": "s", "rationale": "r", "coach_note": "c",
        "workouts": [{"sport": "running", "title": "Neighborhood shakeout loop",
                      "total_time": "30 min", "distance_km": 5.0,
                      "steps": [{"type": "main", "zone": 2, "duration": "30:00"}]}],
    }
    report = audit_plan(easy_unknown, _ctx(), availability=AVAIL)
    assert not [v for v in report.hard if v["kind"] == "forbidden_title"]


def test_conditional_forbidden_fires_only_past_threshold():
    short = _week({"Sunday": (14, "Long Run")})
    report = audit_plan(short, _ctx(), availability=AVAIL)
    assert not [v for v in report.hard if v["kind"] == "forbidden_title"]

    long = _week({"Sunday": (18, "Long Run")})
    report = audit_plan(long, _ctx(), availability=AVAIL)
    assert any(v["kind"] == "forbidden_title" and v["day"] == "Sunday"
               for v in report.hard)


def test_terminal_downgrade_preserves_volume():
    plan = _week({"Monday": 8, "Saturday": (12, "Marathon Pace Long Run")})
    report = audit_plan(plan, _ctx(), availability=AVAIL)
    plan, repairs = apply_terminal_repairs(plan, report)

    saturday = plan["days"]["Saturday"]["workouts"][0]
    assert saturday["title"] == "Easy Run"
    assert saturday["distance_km"] == 12
    assert saturday["enforced_reason"]
    assert any(r["day"] == "Saturday" for r in repairs)


# ─── B3: quality is counted, not requested ──────────────────────────────────


def test_is_quality_truth_table():
    assert is_quality(_run_workout(8, "Tempo Run"))
    assert not is_quality(_run_workout(5, "Strides/Openers"))
    assert not is_quality(_run_workout(16, "Long Run"))
    assert not is_quality({"sport": "strength", "title": "Heavy Squats",
                           "total_time": "60 min", "steps": []})
    zone4_unknown = {"sport": "running", "title": "Surges", "total_time": "40 min",
                     "steps": [{"type": "main", "zone": 4, "duration": "20:00"}]}
    assert is_quality(zone4_unknown)
    # Strides stay non-quality even with high-zone steps.
    hot_strides = {"sport": "running", "title": "Strides/Openers",
                   "total_time": "20 min",
                   "steps": [{"type": "main", "zone": 5, "duration": "0:30"}]}
    assert not is_quality(hot_strides)


def test_quality_over_cap_is_hard_and_counts_locked_days():
    three_quality = _week({
        "Tuesday": (8, "Tempo Run"),
        "Thursday": (8, "Cruise Intervals"),
        "Saturday": (8, "Tempo Run"),
        "Sunday": 14,
    })
    report = audit_plan(three_quality, _ctx(quality=2), availability=AVAIL)
    v = next(v for v in report.hard if v["kind"] == "quality_count")
    assert "3 quality" in v["detail"]

    # Locked Tuesday tempo counts even when the window excludes it.
    report = audit_plan(three_quality, _ctx(quality=2),
                        days=["Thursday", "Friday", "Saturday", "Sunday"],
                        availability=AVAIL)
    assert any(v["kind"] == "quality_count" for v in report.hard)


def test_quality_repair_keeps_earliest_downgrades_latest():
    three_quality = _week({
        "Tuesday": (8, "Tempo Run"),
        "Thursday": (8, "Cruise Intervals"),
        "Saturday": (8, "Tempo Run"),
        "Sunday": 14,
    })
    km_before = 38
    report = audit_plan(three_quality, _ctx(quality=2), availability=AVAIL)
    plan, repairs = apply_terminal_repairs(three_quality, report)

    assert plan["days"]["Tuesday"]["workouts"][0]["title"] == "Tempo Run"
    assert plan["days"]["Thursday"]["workouts"][0]["title"] == "Cruise Intervals"
    assert plan["days"]["Saturday"]["workouts"][0]["title"] == "Easy Run"
    assert volume_gate.planned_run_km(plan) == km_before  # downgrade, not strip


# ─── B6: a base week without its one quality run gets flagged, softly ───────


def test_quality_missing_is_soft_with_escape_hatches():
    easy_week = _week({"Monday": 8, "Wednesday": 8, "Friday": 8, "Sunday": 14})

    report = audit_plan(easy_week, _ctx(), availability=AVAIL)
    assert any(v["kind"] == "quality_missing" for v in report.soft)
    assert report.ok  # soft never blocks

    with_tempo = _week({"Monday": 8, "Friday": (8, "Tempo Run"), "Sunday": 14})
    report = audit_plan(with_tempo, _ctx(), availability=AVAIL)
    assert not [v for v in report.soft if v["kind"] == "quality_missing"]

    # Each hatch flips the check off independently.
    hatches = [
        _ctx(is_recovery_week=True),
        _ctx(phase="foundation", quality=1),
        _ctx(phase="taper", quality=1),
        _ctx(recovery_status="red"),
    ]
    for ctx in hatches:
        report = audit_plan(easy_week, ctx, availability=AVAIL)
        assert not [v for v in report.soft if v["kind"] == "quality_missing"], ctx["phase"]

    class Injury:
        affected_sports = "run"
        body_part = "calf"
        severity = 5

    report = audit_plan(easy_week, _ctx(), availability=AVAIL,
                        active_injuries=[Injury()])
    assert not [v for v in report.soft if v["kind"] == "quality_missing"]

    # Travel eating the week down to one open day skips the nag.
    travel_avail = dict(AVAIL, travel_day_names=[
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"])
    report = audit_plan(easy_week, _ctx(), availability=travel_avail)
    assert not [v for v in report.soft if v["kind"] == "quality_missing"]


def test_gate_warnings_survive_renormalization(client, test_db_session, monkeypatch):
    thin = _week({"Monday": 5, "Thursday": 5})
    fake, _ = _fake_generator([thin])
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", fake)
    client.get("/weekly-plan")

    # The stored-plan path re-normalizes on every read.
    again = client.get("/weekly-plan").json()
    assert again["week_summary"].get("gate_warnings")
