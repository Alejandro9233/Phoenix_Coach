"""The plan-write pipeline: one choke point, receipts, fresh metadata.

Rules locked in here:
- finalize_plan_write stamps week_summary.expected_total_hours AND
  expected_run_km as the deterministic sums of the planned week (strength
  excluded from hours — gym time never counts toward volume targets). The
  LLM's numbers never survive; the week's TARGET lives separately in
  _context.volume_targets, written only by C3.
- Every write appends a receipt to plan_json["_revisions"] (source, days,
  reason, before-snapshot, what enforcement stripped), capped at
  MAX_REVISIONS, and normalize_plan round-trips it.
- STRUCTURAL: no persist path in main.py or issue_triage.py may write
  plan_json without run_plan_write_pipeline, and neither file may call
  enforce_constraints directly — per-path stage drift is how the
  week_summary staleness bug happened.
"""
import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import ResponseAgent
from backend.main import app, get_db
from backend.models.database import Athlete, Base, WeeklyPlan
from backend.services.plan_meta import (
    MAX_REVISIONS,
    capture_before,
    finalize_plan_write,
)
from backend.services.plan_normalizer import VALID_DAYS, normalize_plan
from backend.utils.timezone import get_local_today

REPO_ROOT = Path(__file__).resolve().parents[2]


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
        race_date=get_local_today() + timedelta(weeks=12),
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


def _day(sport, title, total_time):
    return {
        "summary": title,
        "workouts": [{"sport": sport, "title": title, "steps": [],
                      "total_time": total_time}],
        "rationale": "r",
        "coach_note": "c",
    }


def test_finalize_recomputes_hours_excluding_strength(test_db_session):
    plan = normalize_plan({
        "week_summary": {"focus": "f", "rationale": "r",
                         "expected_total_hours": 99.9, "expected_run_km": 42.0},
        "days": {
            "Monday": _day("running", "Easy Run", "45 min"),
            "Tuesday": _day("cycling", "Spin", "45:00"),
            "Wednesday": _day("strength", "Gym", "60 min"),
        },
    })
    out = finalize_plan_write(
        test_db_session, plan, source="generate",
        days_written=VALID_DAYS, violations=[],
    )
    # 45 + 45 running/cycling minutes; the 60 strength minutes must not count.
    assert out["week_summary"]["expected_total_hours"] == 1.5
    # expected_run_km is the planned sum too — the LLM's 42.0 must not
    # survive. The 45-min Easy Run has no distance, so the gate's estimated
    # tier prices it at 45/6.0 = 7.5 km.
    assert out["week_summary"]["expected_run_km"] == 7.5
    assert "_context" in out


def test_receipts_append_in_order_snapshot_and_cap(test_db_session):
    plan = normalize_plan({"days": {
        "Monday": _day("running", "Easy Run", "45 min"),
        "Tuesday": _day("cycling", "Spin", "60 min"),
    }})
    before = capture_before(plan)
    assert before["Monday"] == [
        {"sport": "running", "title": "Easy Run", "total_time": "45 min"}]

    plan = finalize_plan_write(
        test_db_session, plan, source="generate", days_written=VALID_DAYS,
        violations=[], reason="first")
    plan = finalize_plan_write(
        test_db_session, plan, source="replan_remaining",
        days_written=["Monday"], violations=[{"day": "Monday", "title": "Easy Run",
                                              "reason": "test strip"}],
        reason="second", before=before)

    revs = plan["_revisions"]
    assert [r["reason"] for r in revs] == ["first", "second"]
    assert revs[1]["source"] == "replan_remaining"
    assert revs[1]["days"] == ["Monday"]
    # The before-snapshot is trimmed to the days actually written.
    assert list(revs[1]["before"].keys()) == ["Monday"]
    assert revs[1]["stripped"] == [{"day": "Monday", "title": "Easy Run",
                                    "reason": "test strip"}]

    for i in range(MAX_REVISIONS + 3):
        plan = finalize_plan_write(
            test_db_session, plan, source="generate", days_written=VALID_DAYS,
            violations=[], reason=f"n{i}")
    assert len(plan["_revisions"]) == MAX_REVISIONS
    # Oldest entries fell off the front.
    assert plan["_revisions"][0]["reason"] != "first"


def test_normalize_plan_round_trips_revisions():
    plan = normalize_plan({
        "days": {"Monday": _day("running", "Easy Run", "45 min")},
        "_revisions": [{"source": "generate", "days": [], "reason": None}],
    })
    assert plan["_revisions"] == [{"source": "generate", "days": [], "reason": None}]


def test_replan_receipt_and_fresh_context(client, test_db_session, monkeypatch):
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    remaining = VALID_DAYS[today.weekday():]
    locked = VALID_DAYS[:today.weekday()]

    seeded = normalize_plan({
        "days": {d: _day("running", f"{d} Run", "45 min") for d in VALID_DAYS},
    })
    test_db_session.add(WeeklyPlan(week_start=week_start, athlete_id=1,
                                   plan_json=seeded))

    # Availability changed since the seeded plan's context was written — the
    # stored _context must reflect the NEW value after the replan.
    athlete = test_db_session.query(Athlete).first()
    athlete.swim_days = "sat"
    test_db_session.commit()

    fixed_days = {d: _day("running", "Replanned Run", "60 min") for d in remaining}
    monkeypatch.setattr(
        ResponseAgent, "generate_remaining_days",
        lambda self, **kwargs: {"days": json.loads(json.dumps(fixed_days))})

    resp = client.post("/weekly-plan/replan-remaining")
    assert resp.status_code == 200

    stored = test_db_session.query(WeeklyPlan).order_by(
        WeeklyPlan.id.desc()).first().plan_json

    entry = stored["_revisions"][-1]
    assert entry["source"] == "replan_remaining"
    assert entry["days"] == remaining
    assert sorted(entry["before"].keys(), key=VALID_DAYS.index) == remaining
    assert entry["before"][remaining[0]][0]["title"] == f"{remaining[0]} Run"

    assert stored["_context"]["availability"]["swim_days"] == "sat"

    expected_hours = round((len(locked) * 45 + len(remaining) * 60) / 60, 1)
    assert stored["week_summary"]["expected_total_hours"] == expected_hours


def _functions_writing_plan_json(tree):
    """Function nodes that assign X.plan_json or build WeeklyPlan(plan_json=...)."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = False
        calls_pipeline = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "plan_json":
                        writes = True
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else "")
                if name == "run_plan_write_pipeline":
                    calls_pipeline = True
                if name == "WeeklyPlan" and any(
                        kw.arg == "plan_json" for kw in sub.keywords):
                    writes = True
        if writes:
            yield node.name, calls_pipeline


def test_no_persist_path_bypasses_pipeline():
    for rel in ("backend/main.py", "backend/services/issue_triage.py"):
        src = (REPO_ROOT / rel).read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else "")
                assert name != "enforce_constraints", (
                    f"{rel} calls enforce_constraints directly; every persist "
                    "path must go through run_plan_write_pipeline")

        for func_name, calls_pipeline in _functions_writing_plan_json(tree):
            assert calls_pipeline, (
                f"{rel}:{func_name} writes plan_json without "
                "run_plan_write_pipeline — a persist path skipped the pipeline")
