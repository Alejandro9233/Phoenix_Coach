"""The same-day "second opinion" adaptation.

A sync can rewrite today's RecoverySnapshot row in place (date is its
primary key) AFTER the day's adaptation already ran. Rules locked in here:

- Unchanged (or barely drifted) numbers: the idempotency guard holds — one
  LLM call per day, the second request gets the existing adapted day back.
- A material move (thresholds in main.ADAPT_SUPERSEDE_THRESHOLDS) earns
  exactly ONE superseding re-adaptation, receipted with a "Second opinion"
  reason and the inputs it saw.
- MAX_ADAPTS_PER_DAY caps the day at two writes no matter what the data
  does afterwards.
- Legacy receipts without recorded inputs never supersede (conservative).
"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.main as main_mod
from backend.main import adapt_today_workout
from backend.models.database import (
    Athlete, Base, RecoverySnapshot, WeeklyPlan,
)
from backend.utils.timezone import get_local_now, get_local_today


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(
        name="Test", weight_kg=78.0,
        race_date=get_local_today() + timedelta(weeks=10),
        race_distance="Marathon", target_finish_time="3:10:00",
    ))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _fake_week():
    return {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {
            day: {"summary": "s", "workouts": [
                {"sport": "running", "title": "Easy Run", "total_time": "30 min",
                 "distance_km": 5.0, "steps": []}],
                "rationale": "r", "coach_note": "c"}
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
        },
    }


def _seed(db, hrv=86.0, rhr=51, fatigue=2, ratio=1.0, tib=0.0):
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    db.add(WeeklyPlan(week_start=week_start, athlete_id=1,
                      plan_json=_fake_week()))
    db.add(RecoverySnapshot(date=today, athlete_id=1, hrv_ms=hrv,
                            resting_hr=rhr, fatigue_state=fatigue,
                            load_ratio=ratio, tib=tib))
    db.commit()
    return db.query(WeeklyPlan).first()


@pytest.fixture
def quiet_llm(monkeypatch):
    """No network: adapt_daily returns a marked copy of the day; count calls."""
    calls = []

    def _adapt(self, day, metrics, training_context=None):
        calls.append(1)
        out = dict(day)
        out["summary"] = f"adapted #{len(calls)}"
        return out

    monkeypatch.setattr(
        "backend.agents.response_agent.ResponseAgent.adapt_daily", _adapt)
    monkeypatch.setattr(
        "backend.agents.data_agent.DataAgent.summarize",
        lambda self: "metrics")
    return calls


def _revisions(db):
    plan = db.query(WeeklyPlan).first()
    db.refresh(plan)
    return [r for r in (plan.plan_json.get("_revisions") or [])
            if r.get("source") == "adapt_today"]


def _bump_snapshot(db, **fields):
    snap = db.query(RecoverySnapshot).first()
    for k, v in fields.items():
        setattr(snap, k, v)
    db.commit()


def test_first_adapt_records_inputs(db, quiet_llm):
    _seed(db)
    adapt_today_workout(body=None, db=db)
    revs = _revisions(db)
    assert len(revs) == 1
    assert revs[0]["inputs"]["resting_hr"] == 51
    assert revs[0]["inputs"]["hrv_ms"] == 86.0


def test_unchanged_data_guard_holds(db, quiet_llm):
    _seed(db)
    first = adapt_today_workout(body=None, db=db)
    second = adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 1          # one LLM call only
    assert len(_revisions(db)) == 1     # no second receipt
    assert second["summary"] == first["summary"]


def test_small_drift_guard_holds(db, quiet_llm):
    _seed(db)
    adapt_today_workout(body=None, db=db)
    # All below thresholds: HRV 5ms, RHR 3bpm, ratio 0.15, TIB 5.
    _bump_snapshot(db, hrv_ms=83.0, resting_hr=53, load_ratio=1.1, tib=-4.0)
    adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 1
    assert len(_revisions(db)) == 1


def test_material_change_supersedes_once(db, quiet_llm):
    _seed(db)
    adapt_today_workout(body=None, db=db)
    _bump_snapshot(db, resting_hr=57, hrv_ms=72.0)  # past both thresholds
    superseded = adapt_today_workout(body=None, db=db)

    assert len(quiet_llm) == 2
    revs = _revisions(db)
    assert len(revs) == 2
    assert revs[-1]["reason"].startswith("Second opinion")
    assert "RHR 51→57" in revs[-1]["reason"]
    assert revs[-1]["inputs"]["resting_hr"] == 57
    assert superseded["summary"] == "adapted #2"

    # Third call the same day: capped, even with the data still moving.
    _bump_snapshot(db, hrv_ms=50.0)
    third = adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 2
    assert len(_revisions(db)) == 2
    assert third["summary"] == "adapted #2"


def test_legacy_receipt_without_inputs_never_supersedes(db, quiet_llm):
    plan = _seed(db)
    # A pre-feature adaptation: last_adapted stamped today, receipt has no
    # inputs recorded.
    pj = dict(plan.plan_json)
    pj["_revisions"] = [{
        "at": get_local_now().isoformat(timespec="seconds"),
        "source": "adapt_today", "days": [get_local_now().strftime("%A")],
        "reason": "Today's workout adapted to recovery metrics.",
    }]
    plan.plan_json = pj
    plan.last_adapted = get_local_now().replace(tzinfo=None)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(plan, "plan_json")
    db.commit()

    _bump_snapshot(db, resting_hr=60, hrv_ms=60.0)  # huge move
    adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 0          # guard held, no LLM call
    assert len(_revisions(db)) == 1


def test_helper_ignores_other_sources_and_days(db, quiet_llm):
    """Yesterday's adapt receipts and non-adapt receipts don't count toward
    the cap or provide comparison inputs."""
    plan = _seed(db)
    yesterday = get_local_now() - timedelta(days=1)
    pj = dict(plan.plan_json)
    pj["_revisions"] = [
        {"at": yesterday.isoformat(timespec="seconds"), "source": "adapt_today",
         "inputs": {"resting_hr": 40}},
        {"at": get_local_now().isoformat(timespec="seconds"), "source": "generate"},
    ]
    plan.plan_json = pj
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(plan, "plan_json")
    db.commit()

    assert main_mod._recovery_changed_since_adapt(
        db, plan.plan_json, get_local_today()) is None
