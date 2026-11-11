"""Hand-set weeks stay hand-set: no LLM rebuild rewrites a day it wasn't sent for.

Rules locked in here:
- The morning refresh never auto-adapts a hand-set week (it records why);
  the athlete's own Adaptar hoy still works.
- Travel in a hand-set week rests only the travel days; every other day keeps
  its stored version and the LLM is not asked to rebuild.
- replan-remaining in a hand-set week takes the days from hand_set_week(), no
  LLM call.
- A failed adapt LLM call persists nothing (no receipt, last_adapted
  unchanged): 502 on the manual endpoint, adapt_error on the auto path.
"""
import asyncio
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import ResponseAgent
from backend.main import (
    _run_smart_refresh, adapt_today_workout, replan_remaining_days,
)
from backend.models.database import (
    Athlete, Base, RecoverySnapshot, WeeklyPlan,
)
from backend.services import hand_set_weeks
from backend.services.issue_triage import apply_travel
from backend.utils.timezone import get_local_now, get_local_today

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _day(summary, title="Easy Run", sport="running"):
    return {"summary": summary, "workouts": [
        {"sport": sport, "title": title, "total_time": "30 min",
         "distance_km": 5.0, "steps": []}],
        "rationale": "r", "coach_note": "c"}


def _week(prefix):
    return {"week_summary": {"focus": "f", "rationale": "r"},
            "days": {d: _day(f"{prefix} {d}") for d in DAYS}}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(name="Test", weight_kg=78.0,
                        race_date=get_local_today() + timedelta(weeks=10),
                        race_distance="Marathon", target_finish_time="3:10:00"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _monday(today=None):
    today = today or get_local_today()
    return today - timedelta(days=today.weekday())


def _seed(db, monkeypatch, hand_set, monday=None, prefix="stored"):
    monday = monday or _monday()
    if hand_set:
        monkeypatch.setitem(hand_set_weeks.HAND_SET_WEEKS, monday, _week("hand-set"))
    db.add(WeeklyPlan(week_start=monday, athlete_id=1, plan_json=_week(prefix)))
    db.add(RecoverySnapshot(date=get_local_today(), athlete_id=1, hrv_ms=50.0,
                            resting_hr=60, fatigue_state=4,
                            load_ratio=1.5, tib=-25.0))
    athlete = db.query(Athlete).first()
    athlete.hrv_baseline = 70.0
    db.commit()
    return db.query(WeeklyPlan).first()


@pytest.fixture
def llm(monkeypatch):
    """adapt_daily counter; the summary is stubbed so no network is touched."""
    calls = []

    def _adapt(self, day, metrics, training_context=None):
        calls.append(1)
        out = dict(day)
        out["summary"] = "adapted"
        out["adaptation"] = "Fatigue high - easier day."
        return out

    monkeypatch.setattr(ResponseAgent, "adapt_daily", _adapt)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "metrics")
    return calls


def _stub_scraper(monkeypatch):
    import backend.main as main_mod

    class _EmptyScraper:
        async def scrape_all(self, backfill_days=0):
            return {}

    monkeypatch.setattr(main_mod, "CorosScraper", _EmptyScraper)


# --- A: the morning refresh -------------------------------------------------

def test_auto_adapt_skipped_in_hand_set_week(db, monkeypatch, llm):
    plan = _seed(db, monkeypatch, hand_set=True)
    _stub_scraper(monkeypatch)

    result = asyncio.run(_run_smart_refresh(db))

    adaptation = result["event"]["adaptation"]
    assert llm == []                      # the LLM was never asked
    assert adaptation["adapted"] is False
    assert adaptation["error"] is None
    assert adaptation["skipped_reason"].startswith("Semana escrita a mano")
    db.refresh(plan)
    assert plan.last_adapted is None


def test_manual_adapt_still_works_in_hand_set_week(db, monkeypatch, llm):
    plan = _seed(db, monkeypatch, hand_set=True)

    day = adapt_today_workout(body=None, db=db)

    assert llm == [1]
    assert day["summary"] == "adapted"
    db.refresh(plan)
    assert plan.last_adapted is not None


def test_auto_adapt_still_runs_in_llm_week(db, monkeypatch, llm):
    _seed(db, monkeypatch, hand_set=False)
    _stub_scraper(monkeypatch)

    result = asyncio.run(_run_smart_refresh(db))

    assert result["event"]["adaptation"]["adapted"] is True
    assert llm == [1]


# --- B: LLM rebuild paths ---------------------------------------------------

def test_travel_in_hand_set_week_only_rests_the_travel_day(db, monkeypatch):
    # A fixed Monday so "the rest of the week" has six open days on any run.
    monday = date(2026, 11, 16)
    monkeypatch.setattr("backend.services.issue_triage.get_local_today",
                        lambda: monday)
    monkeypatch.setitem(hand_set_weeks.HAND_SET_WEEKS, monday, _week("hand-set"))
    db.add(WeeklyPlan(week_start=monday, athlete_id=1, plan_json=_week("stored")))
    db.commit()
    llm_calls = []

    def _rebuild(self, **kw):
        llm_calls.append(kw["days_to_plan"])
        return {"days": {d: _day("LLM rewrite") for d in kw["days_to_plan"]},
                "week_summary": {}}

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", _rebuild)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "metrics")
    monkeypatch.setattr(
        "backend.services.periodization_engine.PeriodizationEngine.compute_context",
        lambda self, db: {"availability": {}})

    result = apply_travel(db, [monday + timedelta(days=4)])  # Friday

    assert llm_calls == []
    assert result["rebuilt_days"] == []
    days = db.query(WeeklyPlan).first().plan_json["days"]
    assert days["Friday"]["workouts"][0]["sport"] == "rest"
    for d in DAYS:
        if d != "Friday":
            assert days[d]["summary"] == f"stored {d}"


def test_replan_remaining_in_hand_set_week_skips_the_llm(db, monkeypatch):
    plan = _seed(db, monkeypatch, hand_set=True)

    def _boom(self, **kw):
        raise AssertionError("the LLM must not rebuild a hand-set week")

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", _boom)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "metrics")

    result = replan_remaining_days(db=db)

    assert result["status"] == "replanned"
    today_idx = get_local_today().weekday()
    days = db.query(WeeklyPlan).first().plan_json["days"]
    for d in DAYS[:today_idx]:
        assert days[d]["summary"] == f"stored {d}"
    # The pipeline's long-run swap may reorder the remaining days, so compare
    # the set: every one of them is a hand-set day, none is the stored one.
    assert {days[d]["summary"] for d in DAYS[today_idx:]} == {
        f"hand-set {d}" for d in DAYS[today_idx:]}


# --- C: a failed adapt call is not an adaptation ------------------------------

def _dead_model(monkeypatch):
    def _boom(self, *a, **k):
        raise RuntimeError("model_not_found (simulated)")

    monkeypatch.setattr(ResponseAgent, "_chat_json_with_retry", _boom)
    monkeypatch.setattr("backend.agents.data_agent.DataAgent.summarize",
                        lambda self: "metrics")


def test_adapt_daily_raises_instead_of_echoing_the_plan(monkeypatch):
    _dead_model(monkeypatch)
    with pytest.raises(RuntimeError):
        ResponseAgent().adapt_daily(_day("planned"), "metrics")


def test_manual_adapt_failure_persists_nothing(db, monkeypatch):
    plan = _seed(db, monkeypatch, hand_set=False)
    before = plan.plan_json
    _dead_model(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        adapt_today_workout(body=None, db=db)

    assert exc.value.status_code == 502
    db.refresh(plan)
    assert plan.last_adapted is None
    assert plan.plan_json == before
    assert not (plan.plan_json.get("_revisions"))


def test_auto_adapt_failure_records_error_and_keeps_going(db, monkeypatch):
    plan = _seed(db, monkeypatch, hand_set=False)
    _dead_model(monkeypatch)
    _stub_scraper(monkeypatch)

    result = asyncio.run(_run_smart_refresh(db))

    adaptation = result["event"]["adaptation"]
    assert adaptation["adapted"] is False
    assert "model_not_found" in adaptation["error"]
    db.refresh(plan)
    assert plan.last_adapted is None
