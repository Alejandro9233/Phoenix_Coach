"""The completion gate and the athlete override.

2026-09-02: an evening refresh ingested the workout the athlete had just
finished, the load spike read as fresh fatigue, and the day he had already
trained was re-adapted at 9pm. And the sheet's "Override & Use Original
Plan" button was cosmetic — no endpoint existed. Rules locked in here:

- Once every planned non-rest workout today has a matching activity, the
  day is history: adapt-today returns the day untouched, no LLM call, no
  receipt. Rest days count as done (nothing to make easier).
- A different-sport activity does NOT complete the day — the planned
  session is still ahead, adaptation stays available.
- POST /weekly-plan/use-original-today restores original_workouts, drops
  the adaptation markers, and receipts the write (source "use_original").
- After an override, automatic re-adaptation is blocked for the rest of
  the day no matter how far the numbers move. The athlete outranks the
  trigger math.
- Overriding a day that was never adapted is a 409.
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import adapt_today_workout, use_original_today
from backend.models.database import (
    Activity, Athlete, Base, RecoverySnapshot, WeeklyPlan,
)
from backend.services.compliance import day_training_done
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


def _fake_week(sport="running"):
    return {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {
            day: {"summary": "s", "workouts": [
                {"sport": sport, "title": "Easy Run", "total_time": "30 min",
                 "distance_km": 5.0, "steps": []}],
                "rationale": "r", "coach_note": "c"}
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
        },
    }


def _seed(db, sport="running"):
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    db.add(WeeklyPlan(week_start=week_start, athlete_id=1,
                      plan_json=_fake_week(sport)))
    db.add(RecoverySnapshot(date=today, athlete_id=1, hrv_ms=86.0,
                            resting_hr=51, fatigue_state=4,
                            load_ratio=1.0, tib=0.0))
    db.commit()
    return db.query(WeeklyPlan).first()


def _add_activity(db, sport="running", minutes=32):
    db.add(Activity(
        id=f"test-{sport}-{minutes}", athlete_id=1, sport=sport,
        start_time=get_local_now().replace(tzinfo=None),
        duration_sec=minutes * 60, distance_m=5200,
    ))
    db.commit()


@pytest.fixture
def quiet_llm(monkeypatch):
    calls = []

    def _adapt(self, day, metrics, training_context=None):
        calls.append(1)
        out = dict(day)
        out["summary"] = f"adapted #{len(calls)}"
        out["adaptation"] = "Fatigue high - easier day."
        return out

    monkeypatch.setattr(
        "backend.agents.response_agent.ResponseAgent.adapt_daily", _adapt)
    monkeypatch.setattr(
        "backend.agents.data_agent.DataAgent.summarize",
        lambda self: "metrics")
    return calls


def _revisions(db, source):
    plan = db.query(WeeklyPlan).first()
    db.refresh(plan)
    return [r for r in (plan.plan_json.get("_revisions") or [])
            if r.get("source") == source]


# --- day_training_done unit behavior ---

def test_rest_day_counts_as_done():
    assert day_training_done({"workouts": [{"sport": "Rest"}]}, []) is True
    assert day_training_done({"workouts": []}, []) is True


def test_sport_alias_matches():
    day = {"workouts": [{"sport": "running"}]}
    trail = type("A", (), {"sport": "trail_running"})()
    assert day_training_done(day, [trail]) is True


# --- the gate on adapt-today ---

def test_done_day_is_not_adapted(db, quiet_llm):
    _seed(db)
    _add_activity(db, "running")
    day = adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 0
    assert len(_revisions(db, "adapt_today")) == 0
    assert day["workouts"][0]["title"] == "Easy Run"


def test_untrained_day_still_adapts(db, quiet_llm):
    _seed(db)
    adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 1
    assert len(_revisions(db, "adapt_today")) == 1


def test_other_sport_does_not_complete_the_day(db, quiet_llm):
    _seed(db, sport="running")
    _add_activity(db, "swimming")
    adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 1


def test_post_workout_sync_cannot_supersede(db, quiet_llm):
    """The exact 2026-09-02 sequence: morning adapt, train, evening sync
    moves the numbers past every supersede threshold — but the day is done."""
    _seed(db)
    adapt_today_workout(body=None, db=db)
    _add_activity(db, "running")
    snap = db.query(RecoverySnapshot).first()
    snap.resting_hr, snap.hrv_ms, snap.load_ratio, snap.tib = 60, 60.0, 1.4, -18.0
    db.commit()
    adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 1
    assert len(_revisions(db, "adapt_today")) == 1


# --- the override endpoint ---

def test_override_restores_original_and_receipts(db, quiet_llm):
    _seed(db)
    adapted = adapt_today_workout(body=None, db=db)
    assert adapted["summary"].startswith("adapted")

    restored = use_original_today(db=db)
    assert restored["workouts"][0]["title"] == "Easy Run"
    assert "original_workouts" not in restored
    assert "adaptation" not in restored
    revs = _revisions(db, "use_original")
    assert len(revs) == 1
    assert "override" in revs[0]["reason"].lower()


def test_override_blocks_readaptation_for_the_day(db, quiet_llm):
    _seed(db)
    adapt_today_workout(body=None, db=db)
    use_original_today(db=db)
    # Numbers move past every supersede threshold — the decision holds.
    snap = db.query(RecoverySnapshot).first()
    snap.resting_hr, snap.hrv_ms = 60, 60.0
    db.commit()
    day = adapt_today_workout(body=None, db=db)
    assert len(quiet_llm) == 1
    assert day["workouts"][0]["title"] == "Easy Run"
    assert len(_revisions(db, "adapt_today")) == 1


def test_override_without_adaptation_is_409(db, quiet_llm):
    _seed(db)
    with pytest.raises(HTTPException) as exc:
        use_original_today(db=db)
    assert exc.value.status_code == 409
