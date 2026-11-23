"""Hand-set weeks (hand_set_weeks.py): a week Python writes, not the LLM.

Rules locked in here:
- A hand-set week never calls the LLM's weekly-plan generation.
- It survives the write pipeline: trial paces, step durations and km stay
  as written; easy runs still get the pace-model band; fuel is stamped.
- Every run maps onto a watch course.
- The week's km sit inside RUN_KM_PLAN's cap for that week.
- The Nov 9 marathon week holds its 55 km, 27 km long run and easy warm-ups.
- The Nov 16 peak week: two hard runs, two swims, and a long run whose only
  pace-targeted block on the watch is the marathon-pace finish.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base
from backend.services import coros_watch
from backend.services.hand_set_weeks import HAND_SET_WEEKS, hand_set_week
from backend.services.periodization_engine import RUN_KM_PLAN, RUN_KM_PLAN_CAP
from backend.services.volume_gate import is_quality
from backend.services import plan_flows

TRIAL_WEEK = date(2026, 11, 2)

# Prod's pace model on 2026-11-03 (CDMX-scaled bands, seconds per km).
PACE_MODEL = {"bands": {
    "recovery": {"lo": 419.9, "hi": 451.0, "label": "7:00-7:31/km"},
    "easy": {"lo": 379.5, "hi": 419.9, "label": "6:19-7:00/km"},
    "long_run": {"lo": 357.7, "hi": 388.8, "label": "5:58-6:29/km"},
    "marathon": {"lo": 279.4, "hi": 289.4, "label": "4:39-4:49/km"},
    "tempo": {"lo": 263.3, "hi": 271.3, "label": "4:23-4:31/km"},
    "interval": {"lo": 250.0, "hi": 258.0, "label": "4:10-4:18/km"},
}}


@pytest.fixture
def db(monkeypatch):
    from backend.services import hand_set_weeks
    from backend.services.periodization_engine import PeriodizationEngine

    monkeypatch.setattr(hand_set_weeks, "HAND_SET_WEEKS", HAND_SET_WEEKS)

    monkeypatch.setattr(PeriodizationEngine, "compute_context",
                        lambda self, db: {"pace_model": PACE_MODEL})
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(name="Test", weight_kg=70.0, lthr=177,
                        race_date=date(2026, 12, 13), race_distance="Marathon"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _persist(db, monday=TRIAL_WEEK):
    from backend.services.plan_meta import run_plan_write_pipeline

    plan, violations = run_plan_write_pipeline(
        db, hand_set_week(monday), source="generate",
        availability={"run_days": "mon,tue,wed,thu,fri,sat,sun",
                      "strength_days": "mon,tue,wed,thu,fri,sat"},
        active_injuries=[], reason="test")
    return plan, violations


def _run(plan, day):
    return next(w for w in plan["days"][day]["workouts"] if w["sport"] == "running")


def test_only_the_written_weeks_are_hand_set(db):
    assert hand_set_week(TRIAL_WEEK) is not None
    assert hand_set_week(date(2026, 11, 30)) is None
    week = hand_set_week(TRIAL_WEEK)
    week["days"]["Sunday"]["summary"] = "changed"
    assert HAND_SET_WEEKS[TRIAL_WEEK]["days"]["Sunday"]["summary"] != "changed"


def test_the_pipeline_keeps_the_trial_as_written(db):
    plan, violations = _persist(db)
    assert violations == []
    trial = _run(plan, "Sunday")
    assert trial["title"] == "Half Marathon Time Trial"
    assert trial["pace_target"] == "4:37/km (goal 1:37:30)"
    assert trial["distance_km"] == 24.7
    assert [s["duration"] for s in trial["steps"]] == ["15:00", "97:30", "10:00"]
    assert trial.get("fuel")
    assert is_quality(trial)

    reps = _run(plan, "Wednesday")
    assert reps["pace_target"] == "4:35-4:40/km"
    assert [s["duration"] for s in reps["steps"] if s["type"] == "main"] == ["09:15"] * 3
    assert is_quality(reps)

    pickups = _run(plan, "Friday")
    assert pickups["pace_target"] == "6:19-7:00/km easy · pick-ups 4:37/km"
    assert pickups["distance_km"] == 4.7
    assert _run(plan, "Tuesday")["pace_target"] == "6:19-7:00/km"
    assert not is_quality(_run(plan, "Saturday"))


def test_every_run_maps_to_a_watch_course(db):
    plan, _ = _persist(db)
    for day in ("Tuesday", "Wednesday", "Friday", "Saturday", "Sunday"):
        course = coros_watch.workout_to_course(_run(plan, day))
        assert course["sections"]


def test_the_week_fits_its_planned_km(db):
    plan, _ = _persist(db)
    km = sum(w.get("distance_km") or 0 for d in plan["days"].values()
             for w in d["workouts"] if w["sport"] == "running")
    assert km <= RUN_KM_PLAN[TRIAL_WEEK] * RUN_KM_PLAN_CAP
    assert km >= RUN_KM_PLAN[TRIAL_WEEK] * 0.95


def test_generation_uses_the_hand_set_week_without_the_llm(db, monkeypatch):
    from backend.agents.response_agent import ResponseAgent

    def boom(*a, **k):
        raise AssertionError("the LLM must not write a hand-set week")

    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", boom)
    monkeypatch.setattr(plan_flows, "watch_sync_safe", lambda *a, **k: {"status": "off"})
    plan = plan_flows._generate_weekly_plan(db, TRIAL_WEEK, None, "generate", "test")
    assert _run(plan, "Sunday")["title"] == "Half Marathon Time Trial"


def test_the_marathon_week_holds_its_km_and_long_run(db):
    monday = date(2026, 11, 9)
    plan, violations = _persist(db, monday)
    assert violations == []
    km = sum(w.get("distance_km") or 0 for d in plan["days"].values()
             for w in d["workouts"] if w["sport"] == "running")
    assert RUN_KM_PLAN[monday] * 0.95 <= km <= RUN_KM_PLAN[monday] * RUN_KM_PLAN_CAP
    long_run = _run(plan, "Sunday")
    assert 26.5 <= long_run["distance_km"] <= 27.5
    assert long_run.get("fuel")
    session = _run(plan, "Thursday")
    assert session["pace_target"] == "4:39-4:49/km"
    assert is_quality(session)
    for day in ("Tuesday", "Wednesday", "Thursday", "Saturday", "Sunday"):
        assert coros_watch.workout_to_course(_run(plan, day))["sections"]


def test_the_peak_week_ends_its_long_run_at_marathon_pace(db):
    monday = date(2026, 11, 16)
    plan, violations = _persist(db, monday)
    assert violations == []
    runs = [w for d in plan["days"].values() for w in d["workouts"] if w["sport"] == "running"]
    km = sum(w.get("distance_km") or 0 for w in runs)
    assert RUN_KM_PLAN[monday] * 0.95 <= km <= RUN_KM_PLAN[monday] * RUN_KM_PLAN_CAP
    assert [w["title"] for w in runs if is_quality(w)] == ["Cruise Intervals", "Marathon Pace Long Run"]
    swims = [w for d in plan["days"].values() for w in d["workouts"] if w["sport"] == "swimming"]
    assert [w["total_time"] for w in swims] == ["30 min", "30 min"]

    # Wednesday: six reps on the watch, each at tempo pace, not heart rate.
    reps = coros_watch.workout_to_course(_run(plan, "Wednesday"))["sections"][1]
    assert reps["repeats"] == 6 and reps["sets"][0]["intensityType"] == 2
    assert _run(plan, "Wednesday")["pace_target"] == "4:23-4:31/km"

    # Sunday: the easy 98 minutes carry no pace target; the last 42 do.
    long_run = _run(plan, "Sunday")
    assert long_run.get("fuel")
    easy, finish, _ = coros_watch.workout_to_course(long_run)["sections"]
    assert easy["targetValue"] == 98 * 60 and easy["intensityType"] == 1
    assert finish["targetValue"] == 42 * 60
    assert (finish["intensityValueStart"], finish["intensityValueEnd"]) == (279, 289)


def test_a_replan_takes_the_hand_set_summary_too(db):
    from backend.services.hand_set_guard import overlay_hand_set_days

    stored = {"week_summary": {"focus": "old", "rationale": "names a Tempo Run", "expected_run_km": 58.3},
              "days": {"Monday": {"summary": "lived"}}, "_revisions": [1]}
    merged = overlay_hand_set_days(stored, date(2026, 11, 16), ["Friday", "Sunday"])
    assert merged["week_summary"]["focus"] == HAND_SET_WEEKS[date(2026, 11, 16)]["week_summary"]["focus"]
    assert "Tempo" not in merged["week_summary"]["rationale"]
    assert merged["week_summary"]["expected_run_km"] == 58.3
    assert merged["days"]["Monday"] == {"summary": "lived"} and merged["_revisions"] == [1]
    assert stored["week_summary"]["focus"] == "old"


def test_the_week_after_the_30_km_is_60_km_all_easy(db):
    monday = date(2026, 11, 23)
    plan, violations = _persist(db, monday)
    assert violations == []
    workouts = [(d, w) for d, day in plan["days"].items() for w in day["workouts"]]
    runs = [w for _, w in workouts if w["sport"] == "running"]
    km = sum(w.get("distance_km") or 0 for w in runs)
    assert RUN_KM_PLAN[monday] == 60.0
    assert RUN_KM_PLAN[monday] * 0.95 <= km <= RUN_KM_PLAN[monday] * RUN_KM_PLAN_CAP
    assert not [w for w in runs if is_quality(w)]
    assert not [w for _, w in workouts if w["sport"] == "swimming"]
    for _, w in workouts:
        if w["sport"] == "strength":
            assert "No press" in w["steps"][1]["description"]
    long_run = _run(plan, "Sunday")
    assert long_run["total_time"] == "120 min" and long_run.get("fuel")
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Saturday", "Sunday"):
        assert coros_watch.workout_to_course(_run(plan, day))["sections"]
