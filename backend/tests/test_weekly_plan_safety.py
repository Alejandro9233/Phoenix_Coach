"""Regression tests for the 2026-08-17 silent-fallback weekly plan.

`llama-3.3-70b-versatile` was retired by Groq. `generate_weekly_plan` caught
the 404 and returned `_fallback_weekly_plan` — a rule-based template — and
`GET /weekly-plan` persisted it as the athlete's actual week ("Base Building,
Standard rule-based plan...") with a 200 and no error surfaced. The replan
path got the fail-loudly rule that day; this path didn't, until now.

Rules locked in here:
- A failed weekly generation fails the request (502) and persists nothing.
- A failed regenerate keeps the existing week (the delete must not commit
  before generation succeeds).
- The agent raises instead of returning a fabricated plan; the fallback
  generator stays deleted.
- Malformed JSON gets exactly one retry before failing.
- No race, no plan: generation requires race_date AND race_distance (409,
  reason "profile_incomplete") — without them the engine defaulted to a
  Marathon week for the auto-created "New Athlete". Stored plans still serve.
"""
import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app, get_db
from backend.models.database import Base, Athlete, WeeklyPlan
from backend.agents.response_agent import ResponseAgent
from backend.utils.timezone import get_local_today

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _real_week():
    return {
        "days": {
            d: {
                "summary": f"{d} real session",
                "workouts": [
                    {"sport": "run", "title": f"{d} run", "steps": [], "total_time": "45:00"}
                ],
                "rationale": "real rationale",
                "coach_note": "real note",
            }
            for d in DAY_NAMES
        }
    }


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

    # Race fields set: generation is gated on them, and most tests here
    # exercise the generation branch, not the guard.
    athlete = Athlete(
        name="Test Athlete",
        weekly_hours_target=8.0,
        race_date=get_local_today() + timedelta(weeks=12),
        race_distance="Marathon",
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


def _week_start():
    today = get_local_today()
    return today - timedelta(days=today.weekday())


def _insert_real_week(session):
    athlete = session.query(Athlete).first()
    session.add(
        WeeklyPlan(week_start=_week_start(), athlete_id=athlete.id, plan_json=_real_week())
    )
    session.commit()


def _saved_plan(session):
    return (
        session.query(WeeklyPlan)
        .filter(WeeklyPlan.week_start == _week_start())
        .order_by(WeeklyPlan.id.desc())
        .first()
    )


def test_failed_generation_returns_502_and_persists_nothing(client, test_db_session, monkeypatch):
    """A dead model must not fabricate a week — no plan row, loud error."""

    def boom(self, *args, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", boom)

    response = client.get("/weekly-plan")

    assert response.status_code == 502, response.text
    assert "no plan persisted" in response.json()["detail"]
    assert _saved_plan(test_db_session) is None


def test_failed_regenerate_keeps_existing_week(client, test_db_session, monkeypatch):
    """Regenerate deletes before it generates — a failure must roll that back."""
    _insert_real_week(test_db_session)

    def boom(self, *args, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", boom)

    response = client.post("/weekly-plan/regenerate")

    assert response.status_code == 502, response.text

    plan = _saved_plan(test_db_session)
    assert plan is not None, "regenerate lost the existing week on failure"
    days = plan.plan_json.get("days", {})
    for name in DAY_NAMES:
        assert days[name]["summary"] == f"{name} real session"


def test_failed_regenerate_with_expired_injury_keeps_existing_week(client, test_db_session, monkeypatch):
    """DataAgent.summarize runs while regenerate's delete is deliberately
    uncommitted. The Active→Recovering flip it triggers for an expired injury
    must not commit — a commit there persists the delete, and a failed
    generation then loses the athlete's week despite the rollback."""
    from backend.models.database import InjuryLog

    _insert_real_week(test_db_session)
    athlete = test_db_session.query(Athlete).first()
    test_db_session.add(
        InjuryLog(
            athlete_id=athlete.id,
            date_reported=get_local_today() - timedelta(days=10),
            body_part="Left calf",
            status="Active",
            severity=4,
            affected_sports="run",
            expected_recovery_date=get_local_today() - timedelta(days=1),
        )
    )
    test_db_session.commit()

    def boom(self, *args, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", boom)

    response = client.post("/weekly-plan/regenerate")

    assert response.status_code == 502, response.text

    plan = _saved_plan(test_db_session)
    assert plan is not None, "the injury-expiry flip committed the plan delete"
    assert plan.plan_json["days"]["Monday"]["summary"] == "Monday real session"


def test_agent_raises_rather_than_fallback(monkeypatch):
    """The agent itself must propagate, not hand back a template plan."""
    import backend.agents.response_agent as ra

    def dead_model(*args, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ra, "chat_completion", dead_model)

    with pytest.raises(Exception) as excinfo:
        ResponseAgent().generate_weekly_plan(
            "summary", {}, training_context={"phase_name": "Foundation"}
        )

    assert "model_not_found" in str(excinfo.value)


def test_fallback_generator_stays_deleted():
    """_fallback_weekly_plan let a dead model ship a template week. Deleted
    2026-08-18 — do not re-add (see CLAUDE.md's removed-on-purpose list)."""
    assert not hasattr(ResponseAgent, "_fallback_weekly_plan")


def _clear_race(session, *fields):
    athlete = session.query(Athlete).first()
    for f in fields:
        setattr(athlete, f, None)
    session.commit()


def _never_called(self, *args, **kwargs):
    raise AssertionError("generate_weekly_plan must not run without a race configured")


def test_no_race_date_returns_409_without_generating(client, test_db_session, monkeypatch):
    """Missing race → 409 profile_incomplete, no row, and the LLM never runs."""
    _clear_race(test_db_session, "race_date", "race_distance")
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", _never_called)

    response = client.get("/weekly-plan")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "profile_incomplete"
    assert set(detail["missing"]) == {"race_date", "race_distance"}
    assert _saved_plan(test_db_session) is None


def test_missing_race_distance_alone_returns_409(client, test_db_session, monkeypatch):
    """race_date without race_distance is still incomplete — the 409 names it."""
    _clear_race(test_db_session, "race_distance")
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", _never_called)

    response = client.get("/weekly-plan")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["missing"] == ["race_distance"]
    assert _saved_plan(test_db_session) is None


def test_complete_race_profile_generates_and_persists(client, test_db_session, monkeypatch):
    """Both race fields set → generation runs and the plan persists."""
    monkeypatch.setattr(
        ResponseAgent, "generate_weekly_plan", lambda self, *args, **kwargs: _real_week()
    )

    response = client.get("/weekly-plan")

    assert response.status_code == 200, response.text
    assert _saved_plan(test_db_session) is not None


def test_incomplete_profile_still_serves_stored_plan(client, test_db_session, monkeypatch):
    """The guard gates generation only — an existing week is always readable."""
    _insert_real_week(test_db_session)
    _clear_race(test_db_session, "race_date", "race_distance")
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", _never_called)

    response = client.get("/weekly-plan")

    assert response.status_code == 200, response.text
    assert response.json()["days"]["Monday"]["summary"] == "Monday real session"


def test_regenerate_with_incomplete_profile_keeps_existing_week(client, test_db_session, monkeypatch):
    """Regenerate pre-deletes the week; the 409 must roll that delete back."""
    _insert_real_week(test_db_session)
    _clear_race(test_db_session, "race_date", "race_distance")
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan", _never_called)

    response = client.post("/weekly-plan/regenerate")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason"] == "profile_incomplete"

    plan = _saved_plan(test_db_session)
    assert plan is not None, "regenerate lost the existing week on the guard"
    assert plan.plan_json["days"]["Monday"]["summary"] == "Monday real session"


def test_malformed_json_retries_once_then_succeeds(monkeypatch):
    """json_mode output occasionally truncates; one retry, then real failure."""
    import backend.agents.response_agent as ra

    calls = []

    def flaky(messages, json_mode=False, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return '{"week_summary": {"focus": "truncat'
        return '{"week_summary": {"focus": "ok"}, "days": {}}'

    monkeypatch.setattr(ra, "chat_completion", flaky)

    plan = ResponseAgent().generate_weekly_plan(
        "summary", {}, training_context={"phase_name": "Foundation"}
    )

    assert len(calls) == 2
    assert plan["week_summary"]["focus"] == "ok"
