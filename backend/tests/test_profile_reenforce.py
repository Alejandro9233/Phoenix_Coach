"""PUT /athlete/profile re-enforces the stored week when availability changes.

The ghost-swim bug: disable a sport in Profile and the stored plan kept its
sessions — the prompt was told, nothing checked. Rules locked in here:
- An availability change strips now-forbidden future sessions (rest with an
  enforced_reason), past days stay byte-for-byte untouched.
- A non-availability save (weight) never touches the plan row.
- No plan row -> the save still succeeds, nothing else happens.
- The response reports what was stripped in plan_enforcement.
"""
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.main as main_module
from backend.main import app, get_db
from backend.models.database import Athlete, Base, WeeklyPlan
from backend.services.plan_normalizer import VALID_DAYS, normalize_plan

# A fixed Wednesday, so "past days" (Mon-Tue) and "future days" (Wed-Sun)
# are deterministic regardless of when the suite runs.
FROZEN_TODAY = date(2026, 8, 19)
WEEK_START = FROZEN_TODAY - timedelta(days=FROZEN_TODAY.weekday())


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
        race_date=FROZEN_TODAY + timedelta(weeks=12),
        race_distance="Marathon",
        swim_days="wed,sat,sun",
        bike_days="mon,tue,wed,thu,fri,sat,sun",
        run_days="mon,tue,wed,thu,fri,sat,sun",
        strength_days="mon,tue,wed,thu,fri,sat,sun",
    )
    session.add(athlete)
    session.commit()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def client(test_db_session, monkeypatch):
    monkeypatch.setattr(main_module, "get_local_today", lambda: FROZEN_TODAY)

    def override_get_db():
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _strength_week():
    return normalize_plan({
        "days": {
            d: {
                "summary": f"{d} gym",
                "workouts": [{"sport": "strength", "title": f"{d} Strength",
                              "steps": [], "total_time": "60 min",
                              "muscle_groups": ["legs"]}],
                "rationale": "r",
                "coach_note": "c",
            }
            for d in VALID_DAYS
        },
    })


def _seed_plan(session):
    seeded = _strength_week()
    session.add(WeeklyPlan(week_start=WEEK_START, athlete_id=1,
                           plan_json=json.loads(json.dumps(seeded))))
    session.commit()
    return seeded


def test_availability_change_strips_future_days_only(client, test_db_session):
    seeded = _seed_plan(test_db_session)

    resp = client.put("/athlete/profile", json={"strength_days": "mon"})
    assert resp.status_code == 200
    enforcement = resp.json()["plan_enforcement"]
    assert enforcement["stripped"] == 5  # Wednesday..Sunday
    assert len(enforcement["details"]) == 5

    stored = test_db_session.query(WeeklyPlan).order_by(
        WeeklyPlan.id.desc()).first().plan_json

    # Past days are history — byte-for-byte untouched.
    for d in ("Monday", "Tuesday"):
        assert stored["days"][d] == seeded["days"][d]

    # Future days became explained rest, not silent gaps.
    for d in VALID_DAYS[2:]:
        workout = stored["days"][d]["workouts"][0]
        assert workout["sport"] == "rest"
        assert workout.get("enforced_reason")

    entry = stored["_revisions"][-1]
    assert entry["source"] == "profile_reenforce"
    assert entry["days"] == VALID_DAYS[2:]
    assert entry["before"]["Wednesday"][0]["title"] == "Wednesday Strength"


def test_non_availability_save_never_touches_the_plan(client, test_db_session):
    seeded = _seed_plan(test_db_session)

    resp = client.put("/athlete/profile", json={"weight_kg": 70})
    assert resp.status_code == 200
    assert "plan_enforcement" not in resp.json()

    stored = test_db_session.query(WeeklyPlan).order_by(
        WeeklyPlan.id.desc()).first().plan_json
    assert stored == seeded


def test_no_op_availability_save_does_not_reenforce(client, test_db_session):
    seeded = _seed_plan(test_db_session)

    # Same value as the fixture — autosave resends unchanged fields.
    resp = client.put("/athlete/profile",
                      json={"strength_days": "mon,tue,wed,thu,fri,sat,sun"})
    assert resp.status_code == 200
    assert "plan_enforcement" not in resp.json()

    stored = test_db_session.query(WeeklyPlan).order_by(
        WeeklyPlan.id.desc()).first().plan_json
    assert stored == seeded


def test_availability_change_without_plan_row_is_fine(client, test_db_session):
    resp = client.put("/athlete/profile", json={"strength_days": "mon"})
    assert resp.status_code == 200
    assert "plan_enforcement" not in resp.json()


def test_compliant_change_does_not_churn_the_row(client, test_db_session):
    seeded = _seed_plan(test_db_session)

    # Strength stays allowed everywhere; only swim days change, and the seeded
    # week holds no swims — nothing to strip, so the row must not be rewritten.
    resp = client.put("/athlete/profile", json={"swim_days": "sat"})
    assert resp.status_code == 200
    assert resp.json()["plan_enforcement"]["stripped"] == 0

    stored = test_db_session.query(WeeklyPlan).order_by(
        WeeklyPlan.id.desc()).first().plan_json
    assert stored == seeded
