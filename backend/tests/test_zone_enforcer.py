"""Step zones are stamped by Python on every persist path.

Rules locked in here:
- Zones come from the athlete's COROS table; LTHR fractions are the fallback;
  neither -> no-op.
- main = the hr_target midpoint's zone; warmup/cooldown = 1; strides = 3.
- Without a bpm range the title's band decides (tempo 4, long run 2).
- Running only; strength and rest are untouched; days scoping holds.
- The pipeline stamps from the athlete row, so a persisted week never
  carries the LLM's copied "zone": 1.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base
from backend.services.plan_meta import run_plan_write_pipeline
from backend.services.zone_enforcer import enforce_zones, zone_ceilings, zone_for_bpm

# Alex's watch table on 2026-10-12: Z1 <=142, Z2 <=159, Z3 <=168, Z4 <=181, Z5 <=188.
HR_ZONES = [{"hr": 142, "index": 0, "ratio": 80.0}, {"hr": 159, "index": 1, "ratio": 90.0},
            {"hr": 168, "index": 2, "ratio": 95.0}, {"hr": 181, "index": 3, "ratio": 102.0},
            {"hr": 188, "index": 4, "ratio": 106.0}, {"hr": 451, "index": 5, "ratio": 255.0}]


def _steps(*zones):
    types = ["warmup", "main", "recovery", "cooldown"]
    descs = ["Easy jog", "Steady easy pace", "4 x 20-sec strides, 40-sec jog recovery", "Very easy jog + stretch"]
    return [{"type": t, "duration": "05:00", "zone": z, "description": d}
            for t, z, d in zip(types, zones, descs)]


def _plan(workouts_by_day):
    return {"week_summary": {"focus": "f", "rationale": "r"},
            "days": {day: {"summary": "s", "rationale": "r", "coach_note": "c", "workouts": ws}
                     for day, ws in workouts_by_day.items()}}


def test_ceilings_from_watch_table_then_lthr_then_nothing():
    assert zone_ceilings(HR_ZONES) == [142, 159, 168, 181, 188, 451]
    assert zone_ceilings(None, 177) == [143, 159, 166, 177]
    assert zone_ceilings([{"index": "x"}], None) is None
    assert zone_ceilings(None, None) is None


def test_zone_for_bpm_on_the_watch_table():
    c = zone_ceilings(HR_ZONES)
    assert zone_for_bpm(130, c) == 1
    assert zone_for_bpm(142, c) == 1      # on the ceiling stays in the zone
    assert zone_for_bpm(145, c) == 2
    assert zone_for_bpm(165, c) == 3
    assert zone_for_bpm(300, c) == 6      # never above COROS's six


def test_easy_run_main_is_z2_strides_z3_edges_z1():
    run = {"sport": "running", "title": "Easy Run", "total_time": "45 min",
           "hr_target": "140-150 bpm", "steps": _steps(1, 1, 1, 1)}
    plan, fixes = enforce_zones(_plan({"Tuesday": [run]}), hr_zones=HR_ZONES)
    zones = [s["zone"] for s in plan["days"]["Tuesday"]["workouts"][0]["steps"]]
    assert zones == [1, 2, 3, 1]
    assert [(f["step"], f["found"], f["set"]) for f in fixes] == [("main", 1, 2), ("recovery", 1, 3)]


def test_long_run_range_is_z2_and_a_right_plan_reports_nothing():
    run = {"sport": "running", "title": "Long Run", "total_time": "75 min",
           "hr_target": "140-155 bpm", "steps": _steps(1, 2, 3, 1)}
    _, fixes = enforce_zones(_plan({"Saturday": [run]}), hr_zones=HR_ZONES)
    assert fixes == []


def test_without_a_bpm_range_the_title_band_decides():
    tempo = {"sport": "running", "title": "Tempo Run", "total_time": "40 min", "hr_target": "--",
             "steps": [{"type": "warmup", "duration": "10:00", "zone": 1, "description": "jog"},
                       {"type": "main", "duration": "20:00", "zone": 1, "description": "tempo"},
                       {"type": "cooldown", "duration": "10:00", "zone": 1, "description": "jog"}]}
    long_run = {"sport": "running", "title": "Long Run (Z1-Z2 only)", "total_time": "70 min", "hr_target": "2",
                "steps": [{"type": "main", "duration": "70:00", "zone": 1, "description": "steady"}]}
    plan, _ = enforce_zones(_plan({"Thursday": [tempo], "Sunday": [long_run]}), lthr=177)
    assert [s["zone"] for s in plan["days"]["Thursday"]["workouts"][0]["steps"]] == [1, 4, 1]
    assert plan["days"]["Sunday"]["workouts"][0]["steps"][0]["zone"] == 2


def test_strength_and_rest_are_untouched_and_days_scope_holds():
    strength = {"sport": "strength", "title": "Maintenance", "total_time": "55 min", "hr_target": "120-135 bpm",
                "steps": [{"type": "main", "duration": "45:00", "zone": 2, "description": "heavy compounds"}]}
    run = {"sport": "running", "title": "Easy Run", "total_time": "45 min", "hr_target": "140-150 bpm",
           "steps": _steps(1, 1, 1, 1)}
    plan, fixes = enforce_zones(_plan({"Monday": [strength], "Tuesday": [run], "Thursday": [dict(run, steps=_steps(1, 1, 1, 1))],
                                       "Wednesday": [{"sport": "rest", "title": "Rest", "total_time": "0 min", "steps": []}]}),
                                hr_zones=HR_ZONES, days=["Monday", "Tuesday", "Wednesday"])
    assert plan["days"]["Monday"]["workouts"][0]["steps"][0]["zone"] == 2
    assert [s["zone"] for s in plan["days"]["Tuesday"]["workouts"][0]["steps"]] == [1, 2, 3, 1]
    assert [s["zone"] for s in plan["days"]["Thursday"]["workouts"][0]["steps"]] == [1, 1, 1, 1]
    assert {f["day"] for f in fixes} == {"Tuesday"}


def test_no_anchors_is_a_no_op():
    run = {"sport": "running", "title": "Easy Run", "total_time": "45 min", "hr_target": "140-150 bpm",
           "steps": _steps(1, 1, 1, 1)}
    plan, fixes = enforce_zones(_plan({"Tuesday": [run]}))
    assert fixes == []
    assert [s["zone"] for s in plan["days"]["Tuesday"]["workouts"][0]["steps"]] == [1, 1, 1, 1]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    session.add(Athlete(name="Alex", race_date=None, race_distance="Marathon", lthr=177, hr_zones=HR_ZONES))
    session.commit()
    yield session
    session.close()


def test_pipeline_stamps_from_the_athlete_row(db):
    run = {"sport": "running", "title": "Easy Run", "total_time": "45 min", "hr_target": "140-150 bpm",
           "steps": _steps(1, 1, 1, 1)}
    plan, violations = run_plan_write_pipeline(
        db, _plan({"Tuesday": [run]}), source="test", availability={}, active_injuries=[])
    assert violations == []
    # The week's only run is its long run, so the pipeline moved it to Sunday
    # (long_run_day.py); the zones travel with it.
    assert [s["zone"] for s in plan["days"]["Sunday"]["workouts"][0]["steps"]] == [1, 2, 3, 1]
