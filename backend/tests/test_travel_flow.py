"""Tests for the travel triage flow (logistics through the same chat door).

Injuries and recoveries had a chat path; "I'm traveling Friday and Saturday"
did not — the athlete's only option was to silently skip sessions and eat the
compliance hit. These tests cover the third door: detect travel, map it to
remaining days of THIS week, rest those days deterministically, and rebuild
the open days with run kilometers as the protected quantity.

Date discipline: everything is computed relative to get_local_today() so the
suite passes on any weekday, including a Sunday run where only one day of the
week remains.
"""
import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app, get_db
from backend.models.database import Base, Athlete, TravelDay, WeeklyPlan
from backend.agents.response_agent import ResponseAgent
from backend.services.constraint_enforcer import (
    enforce_constraints,
    get_travel_day_names,
)
from backend.services.issue_triage import (
    apply_travel,
    build_travel_proposal,
    extract_travel,
    looks_like_travel,
)
from backend.utils.timezone import get_local_today

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _remaining_week():
    """(today, week_start, [(day_name, date), ...] for today→Sunday)."""
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    remaining = [
        (DAY_NAMES[offset], week_start + timedelta(days=offset))
        for offset in range(today.weekday(), 7)
    ]
    return today, week_start, remaining


def _day(sport, title):
    return {
        "summary": f"{title} day",
        "workouts": [{"sport": sport, "title": title, "steps": [], "total_time": "45:00"}],
        "rationale": "train",
        "coach_note": "go",
    }


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    athlete = Athlete(name="Test Athlete", weekly_hours_target=8.0)
    session.add(athlete)
    session.commit()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_week(session):
    """A plan where every remaining day holds real training: a run on the
    first remaining day, strength on the others."""
    _, week_start, remaining = _remaining_week()
    days = {name: _day("rest", "Rest") for name in DAY_NAMES}
    for i, (name, _d) in enumerate(remaining):
        days[name] = _day("running", "Long Run") if i == 0 else _day("strength", "Hypertrophy")
    plan = WeeklyPlan(week_start=week_start, athlete_id=1, plan_json={"days": days})
    session.add(plan)
    session.commit()
    return plan


# ─── 1. detect ───────────────────────────────────────────────────────────────

def test_travel_pattern_gate():
    assert looks_like_travel("I'm traveling friday and saturday")
    assert looks_like_travel("flying out thursday night")
    assert looks_like_travel("tengo un viaje el fin de semana")
    assert looks_like_travel("estaré fuera de la ciudad el viernes")
    assert looks_like_travel("viajo el jueves por trabajo")
    assert looks_like_travel("tengo vuelo el sábado temprano")
    assert not looks_like_travel("how did my week go?")
    assert not looks_like_travel("my calf hurts")
    assert not looks_like_travel("")


def test_extract_travel_filters_to_remaining_week(db_session, monkeypatch):
    import backend.core.llm_client as llm

    _, _, remaining = _remaining_week()
    valid_day = remaining[0][0]

    monkeypatch.setattr(
        llm, "chat_completion",
        lambda **kw: f'{{"is_travel": true, "days": ["{valid_day}", "Blursday"]}}',
    )
    assert extract_travel("i'm away") == [valid_day]

    monkeypatch.setattr(llm, "chat_completion", lambda **kw: '{"is_travel": false}')
    assert extract_travel("what a trip that race was") is None


def test_extract_travel_failure_is_none(monkeypatch):
    import backend.core.llm_client as llm

    def boom(**kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(llm, "chat_completion", boom)
    assert extract_travel("traveling friday") is None


# ─── 2. propose (read-only) ──────────────────────────────────────────────────

def test_build_travel_proposal_lists_affected(db_session):
    _seed_week(db_session)
    _, _, remaining = _remaining_week()
    blocked = [remaining[0][0]]  # the day holding the run

    proposal = build_travel_proposal(db_session, blocked)

    assert proposal["type"] == "travel_proposal"
    assert proposal["days"] == blocked
    assert proposal["displaced_runs"] == 1
    assert proposal["affected_days"][0]["workouts"] == ["Long Run"]
    assert blocked[0] not in proposal["rebuild_days"]
    # Proposal writes nothing.
    assert db_session.query(TravelDay).count() == 0


# ─── 3. apply ────────────────────────────────────────────────────────────────

def test_apply_travel_rests_days_and_rebuilds(db_session, monkeypatch):
    plan = _seed_week(db_session)
    today, week_start, remaining = _remaining_week()
    blocked_name, blocked_date = remaining[0]

    monkeypatch.setattr(
        ResponseAgent, "generate_remaining_days",
        lambda self, **kw: {"days": {d: _day("running", "Moved Run") for d in kw["days_to_plan"]}},
    )

    result = apply_travel(db_session, [blocked_date.isoformat()], note="work trip")

    assert result["status"] == "applied"
    assert result["travel_days"] == [blocked_name]
    rows = db_session.query(TravelDay).all()
    assert [r.date for r in rows] == [blocked_date]

    db_session.refresh(plan)
    blocked_day = plan.plan_json["days"][blocked_name]
    assert all(w["sport"] == "rest" for w in blocked_day["workouts"])
    assert "travel" in blocked_day["summary"].lower()

    open_names = [name for name, _d in remaining[1:]]
    assert result["rebuilt_days"] == sorted(open_names, key=DAY_NAMES.index)
    assert get_travel_day_names(db_session, 1, week_start) == [blocked_name]


def test_apply_travel_is_idempotent(db_session, monkeypatch):
    _seed_week(db_session)
    _, _, remaining = _remaining_week()
    blocked_date = remaining[0][1]

    monkeypatch.setattr(
        ResponseAgent, "generate_remaining_days",
        lambda self, **kw: {"days": {}},
    )

    apply_travel(db_session, [blocked_date.isoformat()])
    apply_travel(db_session, [blocked_date.isoformat()])
    assert db_session.query(TravelDay).count() == 1


def test_apply_travel_rebuild_failure_still_blocks(db_session, monkeypatch):
    plan = _seed_week(db_session)
    _, _, remaining = _remaining_week()
    blocked_name, blocked_date = remaining[0]

    def boom(self, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", boom)

    result = apply_travel(db_session, [blocked_date.isoformat()])

    # The trip is a fact even when the coach is down: rows written, day rested.
    assert db_session.query(TravelDay).count() == 1
    db_session.refresh(plan)
    assert all(
        w["sport"] == "rest" for w in plan.plan_json["days"][blocked_name]["workouts"]
    )
    if len(remaining) > 1:  # rebuild only exists when open days remain
        assert "rebuild_error" in result
    assert result["rebuilt_days"] == []


def test_apply_travel_rejects_out_of_week_dates(db_session):
    today, _, _ = _remaining_week()
    with pytest.raises(ValueError):
        apply_travel(db_session, [(today - timedelta(days=10)).isoformat()])


# ─── the hard gate ───────────────────────────────────────────────────────────

def test_enforcer_strips_travel_day_workouts():
    plan_json = {"days": {"Friday": _day("strength", "Hypertrophy")}}

    plan_json, violations = enforce_constraints(
        plan_json,
        availability={"travel_day_names": ["Friday"]},
    )

    assert len(violations) == 1
    assert "Traveling" in violations[0]["reason"]
    day = plan_json["days"]["Friday"]
    assert all(w["sport"] == "rest" for w in day["workouts"])


# ─── endpoint ────────────────────────────────────────────────────────────────

def test_apply_endpoint_requires_dates(client):
    assert client.post("/coach/travel/apply", json={}).status_code == 400
    assert client.post("/coach/travel/apply", json={"dates": []}).status_code == 400


def test_apply_endpoint_past_dates_400(client, db_session):
    today = get_local_today()
    response = client.post(
        "/coach/travel/apply",
        json={"dates": [(today - timedelta(days=30)).isoformat()]},
    )
    assert response.status_code == 400
