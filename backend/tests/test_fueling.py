"""Fuel lines: Python numbers stamped post-generation, never LLM-phrased.

Rules locked in here:
- No fuel line under 90 min; 45-60 g/h to 120 min; 60-90 g/h beyond.
- Fluid from body weight (8-10 ml/kg/h) when known, 500-750 default.
- stamp_fuel is idempotent, corrects drift, and CLEARS a stale line when a
  replan shortens the run. Running workouts only.
- plan_normalizer carries "fuel" — without the carry every replan erases it.
- The pipeline stamps fuel on every persist path (pace_target's twin).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base
from backend.services.fueling import fuel_line, stamp_fuel
from backend.services.plan_normalizer import normalize_plan


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(name="Test", weight_kg=78.0,
                        race_date=None, race_distance="Marathon"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _plan(total_time, sport="running"):
    return {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {"Saturday": {"summary": "s", "workouts": [
            {"sport": sport, "title": "Long Run", "total_time": total_time,
             "steps": []}], "rationale": "r", "coach_note": "c"}},
    }


def test_bands():
    assert fuel_line("60 min", 78) is None
    assert fuel_line("89 min", 78) is None
    assert fuel_line("90 min", 78) is not None   # threshold is inclusive
    assert fuel_line("100 min", 78).startswith("45-60 g carbs/h")
    assert fuel_line("120 min", 78).startswith("45-60 g carbs/h")  # boundary
    assert fuel_line("121 min", 78).startswith("60-90 g carbs/h")
    assert fuel_line("150 min", 78).startswith("60-90 g carbs/h")


def test_fluid_from_weight_rounded_to_20():
    line = fuel_line("100 min", 78)
    assert "620-780 ml fluid/h" in line   # 8*78=624->620, 10*78=780
    assert "more in heat" in line


def test_fluid_default_without_weight():
    assert "500-750 ml fluid/h" in fuel_line("100 min", None)


def test_stamp_sets_clears_and_is_idempotent():
    plan = _plan("2:00:00")  # 120 min -> routine band
    plan, changes = stamp_fuel(plan, 78)
    assert len(changes) == 1
    w = plan["days"]["Saturday"]["workouts"][0]
    assert w["fuel"].startswith("45-60 g carbs/h")

    plan, changes = stamp_fuel(plan, 78)
    assert changes == []  # idempotent

    w["total_time"] = "45 min"  # replan shortened the run
    plan, changes = stamp_fuel(plan, 78)
    assert "fuel" not in w
    assert changes[-1]["set"] is None


def test_non_running_never_touched():
    plan = _plan("3:00:00", sport="cycling")
    plan, changes = stamp_fuel(plan, 78)
    assert changes == []
    assert "fuel" not in plan["days"]["Saturday"]["workouts"][0]


def test_normalizer_carries_fuel():
    plan = _plan("100 min")
    plan["days"]["Saturday"]["workouts"][0]["fuel"] = "45-60 g carbs/h · x"
    out = normalize_plan(plan)
    assert out["days"]["Saturday"]["workouts"][0]["fuel"] == "45-60 g carbs/h · x"


def test_pipeline_stamps_fuel_on_persist(db):
    from backend.services.plan_meta import run_plan_write_pipeline

    plan, _ = run_plan_write_pipeline(
        db, _plan("110 min"),
        source="adapt_today",
        availability={"run_days": "mon,tue,wed,thu,fri,sat,sun"},
        active_injuries=[],
        days=["Saturday"],
        reason="test",
    )
    w = plan["days"]["Saturday"]["workouts"][0]
    assert w.get("fuel", "").startswith("45-60 g carbs/h")
    assert "620-780" in w["fuel"]  # athlete weight 78 flowed in
