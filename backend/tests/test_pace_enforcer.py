"""C2: pace_target is stamped by Python on every persist path.

Rules locked in here:
- Classification order (marathon-pace before long-run), unknown -> easy.
- pace_target is force-set to the canonical band label — missing or wrong
  self-heals; no prose is rewritten, no session is ever deleted over a pace.
- days scoping leaves locked days alone; pace_model None is a no-op.
- The goal-vs-fitness note lands in week_summary.rationale exactly once.
- The normalizer carries pace_target (dropping it would erase every pace on
  every replan).
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
from backend.services.pace_enforcer import classify_run_workout, enforce_paces
from backend.services.pace_model import compute_pace_model
from backend.services.plan_normalizer import normalize_plan
from backend.utils.timezone import get_local_today

PM = compute_pace_model(4.5, "3:10:00", "Marathon")


def _plan(day_title_pairs):
    days = {}
    for day, title, sport in day_title_pairs:
        days[day] = {
            "summary": "s", "rationale": "r", "coach_note": "c",
            "workouts": [{"sport": sport, "title": title,
                          "total_time": "45 min", "steps": []}],
        }
    return {"week_summary": {"focus": "f", "rationale": "Base rationale."},
            "days": days}


def test_classification_order():
    assert classify_run_workout("Marathon Pace Long Run") == "marathon"
    assert classify_run_workout("Long Run (Z1-Z2 only)") == "long_run"
    assert classify_run_workout("Cruise Intervals") == "tempo"
    assert classify_run_workout("VO2max Intervals") == "interval"
    assert classify_run_workout("Progressive Run") == "progressive"
    assert classify_run_workout("Recovery jog") == "recovery"
    assert classify_run_workout("Something unheard of") == "easy"


def test_pace_target_force_set_and_scoped():
    plan = _plan([
        ("Monday", "Easy Run", "running"),
        ("Tuesday", "Tempo Run", "running"),
        ("Wednesday", "Endurance Ride (Z2)", "cycling"),
        ("Thursday", "Tempo Run", "running"),
    ])
    plan["days"]["Monday"]["workouts"][0]["pace_target"] = "9:99/km"  # wrong

    plan, fixes = enforce_paces(plan, PM, days=["Monday", "Tuesday", "Wednesday"])

    assert plan["days"]["Monday"]["workouts"][0]["pace_target"] == PM["bands"]["easy"]["label"]
    assert plan["days"]["Tuesday"]["workouts"][0]["pace_target"] == PM["bands"]["tempo"]["label"]
    assert "pace_target" not in plan["days"]["Wednesday"]["workouts"][0]  # cycling
    assert "pace_target" not in plan["days"]["Thursday"]["workouts"][0]  # locked
    assert {f["day"] for f in fixes} == {"Monday", "Tuesday"}


def test_none_pace_model_is_a_noop():
    plan = _plan([("Monday", "Easy Run", "running")])
    out, fixes = enforce_paces(plan, None)
    assert fixes == []
    assert "pace_target" not in out["days"]["Monday"]["workouts"][0]


def test_note_appended_to_rationale_once():
    pm = compute_pace_model(4 + 40 / 60, "3:10:00", "Marathon")
    assert pm["note"]
    plan = _plan([("Monday", "Easy Run", "running")])
    plan, _ = enforce_paces(plan, pm)
    plan, _ = enforce_paces(plan, pm)  # idempotent
    assert plan["week_summary"]["rationale"].count(pm["note"]) == 1


def test_normalizer_carries_pace_target():
    plan = _plan([("Monday", "Easy Run", "running")])
    plan, _ = enforce_paces(plan, PM)
    again = normalize_plan(plan)
    assert again["days"]["Monday"]["workouts"][0]["pace_target"] == \
        PM["bands"]["easy"]["label"]


# ─── E2E: the persisted week carries Python paces ───────────────────────────


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
        target_finish_time="3:10:00",
        threshold_pace_min_km=4.5,
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


def test_generated_week_persists_pace_targets(client, test_db_session, monkeypatch):
    week = {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {
            day: {"summary": "s", "rationale": "r", "coach_note": "c",
                  "workouts": [{"sport": "running", "title": "Easy Run",
                                "total_time": "45 min", "distance_km": 8.0,
                                "steps": [], "pace_target": "3:00/km"}]}
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
        },
    }
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan",
                        lambda self, *a, **kw: dict(week))

    response = client.get("/weekly-plan")
    assert response.status_code == 200, response.text

    today = get_local_today()
    row = (
        test_db_session.query(WeeklyPlan)
        .filter(WeeklyPlan.week_start == today - timedelta(days=today.weekday()))
        .order_by(WeeklyPlan.id.desc())
        .first()
    )
    stored = row.plan_json["days"]["Monday"]["workouts"][0]
    # The LLM's invented "3:00/km" did not survive.
    assert stored["pace_target"] == PM["bands"]["easy"]["label"]


# --- Distances: Python stamps km from time x band ------------------------------
# The week of 2026-10-05 shipped distance_km 4.2 on every run (the prompt's
# example, copied), including a 62-minute long run.
from backend.services.pace_enforcer import (declared_km_is_plausible, enforce_distances,
                                            expected_run_km)


def _run_workout(title, total_time, distance_km=None, steps=None):
    w = {"sport": "running", "title": title, "total_time": total_time, "hr_target": "2",
         "steps": steps or [{"type": "main", "duration": "30:00", "zone": 2, "description": "steady"}]}
    if distance_km is not None:
        w["distance_km"] = distance_km
    return w


def _week(**days):
    base = {d: {"workouts": []} for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}
    for k, v in days.items():
        base[k] = {"workouts": v}
    return {"days": base}


def _mid(band_key):
    b = PM["bands"][band_key]
    return (b["lo"] + b["hi"]) / 2


def test_copied_example_distance_is_replaced_from_time_and_band():
    plan = _week(Wednesday=[_run_workout("Easy Run", "35 min", 4.2)],
                 Friday=[_run_workout("Long Run", "62 min", 4.2)])
    plan, fixes = enforce_distances(plan, PM)
    easy = plan["days"]["Wednesday"]["workouts"][0]["distance_km"]
    long = plan["days"]["Friday"]["workouts"][0]["distance_km"]
    assert easy == round(35 * 60 / _mid("easy"), 1) and easy > 4.2
    assert long == round(62 * 60 / _mid("long_run"), 1) and long > 8
    assert [(f["day"], f["found"], f["basis"]) for f in fixes] == [("Wednesday", 4.2, "pace"), ("Friday", 4.2, "pace")]


def test_plausible_declared_distance_is_kept():
    km = round(35 * 60 / _mid("easy"), 1) - 0.3        # a little slower than mid, inside the band
    plan = _week(Wednesday=[_run_workout("Easy Run", "35 min", km)])
    plan, fixes = enforce_distances(plan, PM)
    assert fixes == [] and plan["days"]["Wednesday"]["workouts"][0]["distance_km"] == km
    assert declared_km_is_plausible(_run_workout("Easy Run", "35 min", km), PM)
    assert not declared_km_is_plausible(_run_workout("Easy Run", "35 min", 4.2), PM)


def test_steps_that_name_every_km_win_over_the_estimate():
    steps = [{"type": "warmup", "duration": "10:00", "zone": 1, "description": "1.5 km jog"},
             {"type": "main", "duration": "40:00", "zone": 2, "description": "6 km steady"},
             {"type": "cooldown", "duration": "08:00", "zone": 1, "description": "1 km walk-jog"}]
    plan = _week(Thursday=[_run_workout("Easy Run", "58 min", 4.2, steps=steps)])
    plan, fixes = enforce_distances(plan, PM)
    assert plan["days"]["Thursday"]["workouts"][0]["distance_km"] == 8.5
    assert fixes[0]["basis"] == "steps"
    assert expected_run_km(plan["days"]["Thursday"]["workouts"][0], PM) == (8.5, "steps")


def test_missing_distance_is_filled_and_rides_are_untouched():
    plan = _week(Tuesday=[_run_workout("Recovery Run", "30 min")],
                 Saturday=[{"sport": "cycling", "title": "Bike Z2", "total_time": "60 min", "distance_km": 4.2, "steps": []}])
    plan, fixes = enforce_distances(plan, PM)
    assert plan["days"]["Tuesday"]["workouts"][0]["distance_km"] == round(30 * 60 / _mid("recovery"), 1)
    assert plan["days"]["Saturday"]["workouts"][0]["distance_km"] == 4.2 and len(fixes) == 1


def test_no_pace_model_or_too_short_is_a_noop_and_stamping_is_idempotent():
    plan = _week(Monday=[_run_workout("Easy Run", "3 min", 4.2)])
    assert enforce_distances(plan, None)[1] == []
    assert enforce_distances(plan, PM)[1] == []           # under DISTANCE_MIN_MINUTES
    plan = _week(Wednesday=[_run_workout("Easy Run", "35 min", 4.2)])
    plan, first = enforce_distances(plan, PM)
    plan, second = enforce_distances(plan, PM)
    assert len(first) == 1 and second == []
