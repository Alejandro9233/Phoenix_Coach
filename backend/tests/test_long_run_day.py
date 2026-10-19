"""The long run lands on Sunday, enforced in the write pipeline (Alex, 2026-10-19).

- A titled long run elsewhere swaps whole days with Sunday; what shared its
  day travels with it, and Sunday's old content takes the vacated day.
- Already on Sunday, no run at all, Sunday outside the days window, or
  Sunday not a run day: nothing moves.
- Without a titled long run the longest run is the long run.
- The pipeline applies it on an ungated write.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base
from backend.services.long_run_day import enforce_long_run_day, find_long_run_day
from backend.services.plan_meta import run_plan_write_pipeline


def _run(title, minutes):
    return {"sport": "running", "title": title, "total_time": f"{minutes} min", "hr_target": "140-150 bpm",
            "steps": [{"type": "main", "duration": f"{minutes}:00", "zone": 2, "description": "steady"}]}


def _strength():
    return {"sport": "strength", "title": "Maintenance", "total_time": "45 min", "hr_target": "120-135 bpm",
            "steps": [{"type": "main", "duration": "45:00", "zone": 2, "description": "compounds"}]}


def _rest():
    return {"sport": "rest", "title": "Rest", "total_time": "0 min", "steps": []}


def _plan(workouts_by_day):
    days = {d: {"summary": f"{d} summary", "rationale": "r", "coach_note": f"{d} note", "workouts": ws}
            for d, ws in workouts_by_day.items()}
    return {"week_summary": {"focus": "f", "rationale": "r"}, "days": days}


ALL_RUN_DAYS = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}


def test_saturday_long_run_swaps_with_sunday_whole_day():
    plan = _plan({"Saturday": [_run("Long Run", 87), _strength()], "Sunday": [_run("Easy Run", 45)]})
    plan, moves = enforce_long_run_day(plan, "Sunday", ALL_RUN_DAYS)
    assert moves == [{"title": "Long Run", "from": "Saturday", "to": "Sunday"}]
    assert [w["title"] for w in plan["days"]["Sunday"]["workouts"]] == ["Long Run", "Maintenance"]
    assert plan["days"]["Sunday"]["coach_note"] == "Saturday note"
    assert [w["title"] for w in plan["days"]["Saturday"]["workouts"]] == ["Easy Run"]


def test_no_ops():
    already = _plan({"Saturday": [_run("Easy Run", 45)], "Sunday": [_run("Long Run", 87)]})
    assert enforce_long_run_day(already, "Sunday", ALL_RUN_DAYS)[1] == []
    no_runs = _plan({"Saturday": [_strength()], "Sunday": [_rest()]})
    assert enforce_long_run_day(no_runs, "Sunday", ALL_RUN_DAYS)[1] == []
    window = _plan({"Saturday": [_run("Long Run", 87)], "Sunday": [_rest()]})
    assert enforce_long_run_day(window, "Sunday", ALL_RUN_DAYS, days=["Friday", "Saturday"])[1] == []
    no_sunday = _plan({"Saturday": [_run("Long Run", 87)], "Sunday": [_rest()]})
    assert enforce_long_run_day(no_sunday, "Sunday", {"run_days": "mon,tue,wed,thu,fri,sat"})[1] == []


def test_longest_run_is_the_long_run_when_none_is_titled():
    plan = _plan({"Tuesday": [_run("Easy Run", 45)], "Thursday": [_run("Easy Run", 70)], "Sunday": [_rest()]})
    assert find_long_run_day(plan) == "Thursday"
    plan, moves = enforce_long_run_day(plan, "Sunday", ALL_RUN_DAYS)
    assert moves[0]["from"] == "Thursday"
    assert plan["days"]["Thursday"]["workouts"][0]["sport"] == "rest"
    assert plan["days"]["Sunday"]["workouts"][0]["total_time"] == "70 min"


def test_pipeline_moves_it_on_a_write():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Athlete(name="Alex", race_distance="Marathon", lthr=177)); db.commit()
    plan = _plan({"Saturday": [_run("Long Run", 87)], "Sunday": [_run("Easy Run", 45)]})
    out, _ = run_plan_write_pipeline(db, plan, source="test", availability=ALL_RUN_DAYS, active_injuries=[],
                                     gate_ctx={"long_run_day": "Sunday"})
    assert out["days"]["Sunday"]["workouts"][0]["title"] == "Long Run"
    assert out["days"]["Saturday"]["workouts"][0]["title"] == "Easy Run"
