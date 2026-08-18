"""Regression tests for the 2026-08-17 replan data loss.

Groq retired `llama-3.3-70b-versatile`. `generate_remaining_days` caught the
404 and returned seven placeholder days ("Error generating plan", no
workouts); `/weekly-plan/replan-remaining` persisted them over a real week and
returned 200. The athlete's whole week was gone with no error surfaced.

The rule these tests lock in: a failed plan generation must fail the request
and leave `plan_json` exactly as it was. Never persist a fallback.
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

    athlete = Athlete(name="Test Athlete", weekly_hours_target=8.0)
    session.add(athlete)
    session.commit()

    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    session.add(
        WeeklyPlan(week_start=week_start, athlete_id=athlete.id, plan_json=_real_week())
    )
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


def _saved_days(session):
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    plan = (
        session.query(WeeklyPlan)
        .filter(WeeklyPlan.week_start == week_start)
        .order_by(WeeklyPlan.id.desc())
        .first()
    )
    return plan.plan_json.get("days", {})


def test_failed_generation_does_not_persist(client, test_db_session, monkeypatch):
    """A dead model must not cost the athlete their week."""

    def boom(self, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", boom)

    response = client.post("/weekly-plan/replan-remaining")

    assert response.status_code == 502, response.text
    assert "left untouched" in response.json()["detail"]

    days = _saved_days(test_db_session)
    for name in DAY_NAMES:
        assert days[name]["summary"] == f"{name} real session"
        assert len(days[name]["workouts"]) == 1


def test_generator_raises_rather_than_returning_placeholders(monkeypatch):
    """The agent itself must propagate, not hand back placeholder days.

    Guards the layer below the endpoint: a fallback return value here is what
    the endpoint would happily persist.
    """
    import backend.agents.response_agent as ra

    def dead_model(*args, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ra, "chat_completion", dead_model)

    with pytest.raises(Exception) as excinfo:
        ResponseAgent().generate_remaining_days(
            athlete_summary="summary",
            profile={},
            training_context={},
            completed_days_summary="nothing yet",
            days_to_plan=["Monday"],
        )

    assert "model_not_found" in str(excinfo.value)
