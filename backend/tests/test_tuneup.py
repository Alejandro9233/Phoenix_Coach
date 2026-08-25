"""Tune-up race gate (lightened C7+UX7 merge).

Rules locked in here:
- The tune race's week is a race week: run target 0.6x, cap 0.65x, no long
  run — and race week OUTRANKS recovery week (they never stack).
- The race is prompted, never Python-injected (B6's invariant): the LLM is
  told to schedule it; Python only scales the volume it will be graded on.
- A past tune race drops out of planning context; its Riegel verdict lives in
  GET /athlete/profile and proposes a target without ever writing one.
- Result detection: same-day running activity within 10% of the declared
  distance, closest match wins.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import _format_training_context
from backend.main import app, get_db
from backend.models.database import Activity, Athlete, Base
from backend.services.pace_model import race_label, tuneup_verdict
from backend.services.periodization_engine import (
    DISTANCE_PROFILES,
    PeriodizationEngine,
)
from backend.utils.timezone import get_local_today

HALF_KM = 21.0975
BUILD_DEF = PeriodizationEngine._phase_def(DISTANCE_PROFILES["Marathon"], {"phase": "build"})


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
        race_date=get_local_today() + timedelta(weeks=10),
        race_distance="Marathon",
        target_finish_time="3:10:00",
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


def _run(session, day, km, dur_sec):
    session.add(Activity(
        id=str(uuid.uuid4()),
        sport="running",
        start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
        distance_m=km * 1000,
        duration_sec=dur_sec,
        source="test",
    ))


# --- pace_model: label + verdict ---

def test_race_label_tolerates_gps_overshoot():
    assert race_label(21.0975) == "Half Marathon"
    assert race_label(21.3) == "Half Marathon"  # GPS half
    assert race_label(10.05) == "10k"
    assert race_label(17.0) == "17 km race"
    assert race_label(None) == "race"


def test_verdict_validates_the_house_rule():
    """1:27 half -> ~3:01 marathon, 8:37 under the 3:10 target."""
    v = tuneup_verdict(5220, HALF_KM, "Marathon", "3:10:00")
    assert v["predicted"] == "3:01:23"
    assert v["goal"] == "3:10:00"
    assert v["delta_sec"] == 10883 - 11400
    assert "8:37 under" in v["summary"]
    assert "Proposed target: 3:01:23" in v["summary"]


def test_verdict_without_goal_still_proposes():
    v = tuneup_verdict(5220, HALF_KM, "Marathon", None)
    assert v["goal"] is None and v["delta_sec"] is None
    assert "Proposed target: 3:01:23" in v["summary"]


def test_verdict_none_for_non_running_goal_or_garbage():
    assert tuneup_verdict(5220, HALF_KM, "70.3", "3:10:00") is None
    assert tuneup_verdict(None, HALF_KM, "Marathon", "3:10:00") is None
    assert tuneup_verdict(5220, 0, "Marathon", "3:10:00") is None


# --- engine: race week scaling + context ---

def test_race_week_outranks_recovery_week(test_db_session):
    """Both flags true -> 0.6x only, never 0.6 x 0.75."""
    t = PeriodizationEngine()._get_weekly_run_target(
        test_db_session, BUILD_DEF, True, get_local_today(), tuneup_week=True,
    )
    floor = float(BUILD_DEF["run_km_range"][0])
    assert t["run_km_target"] == round(floor * 0.6, 1)
    assert t["long_run_minutes"] == 0
    assert "tune-up race week" in t["basis"]


def _set_tune(session, race_date, km=HALF_KM, target="1:27:00"):
    athlete = session.query(Athlete).first()
    athlete.tune_race_date = race_date
    athlete.tune_race_distance_km = km
    athlete.tune_race_target = target
    session.commit()


def test_context_race_week_scales_and_prompts(test_db_session):
    today = get_local_today()
    saturday = today - timedelta(days=today.weekday()) + timedelta(days=5)
    _set_tune(test_db_session, saturday)

    ctx = PeriodizationEngine().compute_context(test_db_session)
    tu = ctx["tuneup"]
    assert tu["is_race_week"] is True
    assert tu["label"] == "Half Marathon"
    assert tu["race_day_name"] == "Saturday"
    assert ctx["volume_targets"]["long_run_minutes"] == 0
    assert "tune-up race week" in ctx["volume_targets"]["basis"]

    text = _format_training_context(ctx)
    assert "TUNE-UP RACE WEEK: Half Marathon on Saturday" in text
    assert "Goal: 1:27:00" in text


def test_context_upcoming_race_is_informational(test_db_session):
    _set_tune(test_db_session, get_local_today() + timedelta(days=21))
    ctx = PeriodizationEngine().compute_context(test_db_session)
    assert ctx["tuneup"]["is_race_week"] is False
    assert "tune-up race week" not in ctx["volume_targets"]["basis"]
    assert "Tune-up race ahead" in _format_training_context(ctx)


def test_context_past_race_drops_out(test_db_session):
    _set_tune(test_db_session, get_local_today() - timedelta(days=10))
    ctx = PeriodizationEngine().compute_context(test_db_session)
    assert ctx["tuneup"] is None
    assert "Tune-up" not in _format_training_context(ctx)


# --- endpoints: round trip + verdict card ---

def test_profile_round_trip(client):
    resp = client.put("/athlete/profile", json={
        "tune_race_date": "2026-10-01",
        "tune_race_distance_km": HALF_KM,
        "tune_race_target": "1:27:00",
    })
    assert resp.status_code == 200

    prof = client.get("/athlete/profile").json()
    assert prof["tune_race_date"] == "2026-10-01"
    assert prof["tune_race_distance_km"] == HALF_KM
    assert prof["tune_race_target"] == "1:27:00"
    assert prof["tuneup"]["result"] is None
    assert prof["tuneup"]["verdict"] is None

    resp = client.put("/athlete/profile", json={"tune_race_date": None})
    assert resp.status_code == 200
    assert client.get("/athlete/profile").json()["tuneup"] is None


def test_profile_verdict_after_race_scraped(client, test_db_session):
    race_day = get_local_today() - timedelta(days=2)
    _set_tune(test_db_session, race_day)
    _run(test_db_session, race_day, 21.3, 5172)          # the race, 1:26:12
    _run(test_db_session, race_day, 3.0, 15 * 60)        # warm-up jog, ignored
    test_db_session.commit()

    tu = client.get("/athlete/profile").json()["tuneup"]
    assert tu["result"]["time"] == "1:26:12"
    assert tu["result"]["distance_km"] == 21.3
    expected = tuneup_verdict(5172, 21.3, "Marathon", "3:10:00")
    assert tu["verdict"]["predicted"] == expected["predicted"]
    assert "under the 3:10:00 target" in tu["verdict"]["summary"]


def test_profile_result_ignores_wrong_distance(client, test_db_session):
    """A 10k on race day is not the half — no result, no verdict."""
    race_day = get_local_today() - timedelta(days=2)
    _set_tune(test_db_session, race_day)
    _run(test_db_session, race_day, 10.0, 40 * 60)
    test_db_session.commit()

    tu = client.get("/athlete/profile").json()["tuneup"]
    assert tu["result"] is None
    assert tu["verdict"] is None
