"""Hand-set weeks (hand_set_weeks.py): a week Python writes, not the LLM.

Rules locked in here:
- A hand-set week never calls the LLM's weekly-plan generation.
- It survives the write pipeline: trial paces, step durations and km stay
  as written; easy runs still get the pace-model band; fuel is stamped.
- Every run maps onto a watch course.
- The week's km sit inside RUN_KM_PLAN's cap for that week.
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


def _persist(db):
    from backend.services.plan_meta import run_plan_write_pipeline

    plan, violations = run_plan_write_pipeline(
        db, hand_set_week(TRIAL_WEEK), source="generate",
        availability={"run_days": "mon,tue,wed,thu,fri,sat,sun",
                      "strength_days": "mon,tue,wed,thu,fri,sat"},
        active_injuries=[], reason="test")
    return plan, violations


def _run(plan, day):
    return next(w for w in plan["days"][day]["workouts"] if w["sport"] == "running")


def test_only_the_trial_week_is_hand_set(db):
    assert hand_set_week(TRIAL_WEEK) is not None
    assert hand_set_week(date(2026, 11, 9)) is None
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
    from backend import main
    from backend.agents.response_agent import ResponseAgent

    def boom(*a, **k):
        raise AssertionError("the LLM must not write a hand-set week")

    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", boom)
    monkeypatch.setattr(main, "_watch_sync_safe", lambda *a, **k: {"status": "off"})
    plan = main._generate_weekly_plan(db, TRIAL_WEEK, None, "generate", "test")
    assert _run(plan, "Sunday")["title"] == "Half Marathon Time Trial"
