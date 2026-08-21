"""C3: the weekly run-km target is computed from actuals, never LLM-authored.

Rules locked in here:
- Target = RUN_RAMP x best of the last 3 weeks' actual run km, clamped to the
  phase floor/ceiling.
- The ramp hard cap (RUN_RAMP_HARD_CAP x demonstrated volume) beats the phase
  floor: thin post-wipe history rebuilds gradually, no cliff-jump.
- Best-of-3, not mean — a missed week can't crater the target.
- Recovery weeks scale the target 0.75x and the cap 0.80x.
- Long run progresses +LONG_RUN_STEP_MIN min/week to the phase cap.
- compute_context carries volume_targets, so every persist path's _context
  refresh (finalize_plan_write) carries it too.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import ResponseAgent, _format_training_context
from backend.main import app, get_db
from backend.models.database import Activity, Athlete, Base, WeeklyPlan
from backend.services.periodization_engine import (
    DISTANCE_PROFILES,
    PeriodizationEngine,
)
from backend.utils.timezone import get_local_today

TODAY = date(2026, 8, 19)  # a Wednesday
MONDAY = TODAY - timedelta(days=TODAY.weekday())

BUILD_DEF = PeriodizationEngine._phase_def(DISTANCE_PROFILES["Marathon"], {"phase": "build"})
BASE_DEF = PeriodizationEngine._phase_def(DISTANCE_PROFILES["Marathon"], {"phase": "base"})


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


def _run(session, day, km, dur_min=None):
    """Seed one running activity on `day`."""
    session.add(Activity(
        id=str(uuid.uuid4()),
        sport="running",
        start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
        distance_m=km * 1000,
        duration_sec=(dur_min or km * 6) * 60,
        source="test",
    ))


def _seed_weeks(session, kms, monday=MONDAY):
    """Seed weekly run volume: kms[0] = 3 weeks ago ... kms[-1] = last week.
    Split into sub-8 km runs so seeding volume never creates long-run history
    (that's the >=8 km tier, seeded explicitly where a test wants it)."""
    for i, km in enumerate(kms):
        week_monday = monday - timedelta(days=7 * (len(kms) - i))
        remaining = km or 0
        day = 0
        while remaining > 0:
            chunk = min(6, remaining)
            _run(session, week_monday + timedelta(days=day % 7), chunk)
            remaining -= chunk
            day += 1
    session.commit()


def _target(session, phase_def, is_recovery_week=False, today=TODAY, **kw):
    return PeriodizationEngine()._get_weekly_run_target(
        session, phase_def, is_recovery_week, today, **kw
    )


def test_ramp_cap_beats_phase_floor(test_db_session):
    """30/34/32 km in Build (40-55): ramp cap 39.1 wins over the 40 floor."""
    _seed_weeks(test_db_session, [30, 34, 32])
    t = _target(test_db_session, BUILD_DEF)
    assert t["run_km_target"] == 39.1  # min(55, max(40, 34*1.08), 34*1.15)
    assert t["rebuild_mode"] is True
    assert t["ramp_base_km"] == 34.0
    assert t["history_weeks"] == 3


def test_no_history_starts_at_phase_floor(test_db_session):
    t = _target(test_db_session, BASE_DEF)
    assert t["run_km_target"] == 28.0
    assert t["run_km_hard_cap"] == 30.8
    assert t["history_weeks"] == 0
    assert t["rebuild_mode"] is False


def test_post_wipe_single_thin_week_rebuilds_gradually(test_db_session):
    """One 22 km week in Base (28-40) -> 25.3, not a cliff-jump to 28."""
    _seed_weeks(test_db_session, [None, None, 22])
    t = _target(test_db_session, BASE_DEF)
    assert t["run_km_target"] == 25.3  # 22 * 1.15
    assert t["run_km_hard_cap"] == 25.3
    assert t["rebuild_mode"] is True


def test_best_of_three_not_mean(test_db_session):
    """A missed week (4 km, under the noise floor) must not crater the target."""
    _seed_weeks(test_db_session, [38, 4, 36])
    t = _target(test_db_session, BUILD_DEF)
    assert t["ramp_base_km"] == 38.0
    assert t["history_weeks"] == 2  # the 4 km week is noise, not evidence


def test_ceiling_clamps_high_volume(test_db_session):
    """60 km demonstrated in Build (40-55): phase ceiling wins."""
    _seed_weeks(test_db_session, [55, 58, 60])
    t = _target(test_db_session, BUILD_DEF)
    assert t["run_km_target"] == 55.0
    assert t["run_km_hard_cap"] == 57.8  # ceiling * 1.05
    assert t["rebuild_mode"] is False


def test_recovery_week_scales_down(test_db_session):
    _seed_weeks(test_db_session, [55, 58, 60])
    t = _target(test_db_session, BUILD_DEF, is_recovery_week=True)
    assert t["run_km_target"] == round(55 * 0.75, 1)
    assert t["run_km_hard_cap"] == round(55 * 1.05 * 0.80, 1)


def test_long_run_progresses_plus_12(test_db_session):
    """100 min long run on record, Build cap 160 -> 112; recovery week -> 78."""
    _seed_weeks(test_db_session, [30, 34, 32])
    _run(test_db_session, MONDAY - timedelta(days=4), 16, dur_min=100)
    test_db_session.commit()

    t = _target(test_db_session, BUILD_DEF)
    assert t["long_run_minutes"] == 112

    t = _target(test_db_session, BUILD_DEF, is_recovery_week=True)
    assert t["long_run_minutes"] == 78  # 112 * 0.7


def test_tuneup_week_hook_scales_and_drops_long_run(test_db_session):
    """C7's race-week hook: 0.6x target, no long run (the race is the long run)."""
    _seed_weeks(test_db_session, [55, 58, 60])
    t = _target(test_db_session, BUILD_DEF, tuneup_week=True)
    assert t["run_km_target"] == round(55 * 0.6, 1)
    assert t["long_run_minutes"] == 0


def test_profile_without_run_range_returns_none(test_db_session):
    """Stub profiles without km in their volume_note degrade to None."""
    stub = {"sport_sessions": {"running": {"volume_note": "easy running only"}}}
    assert _target(test_db_session, stub) is None


def test_compute_context_carries_volume_targets(test_db_session):
    ctx = PeriodizationEngine().compute_context(test_db_session)
    vt = ctx["volume_targets"]
    for key in ("run_km_target", "run_km_floor", "run_km_ceiling",
                "run_km_hard_cap", "long_run_minutes", "history_weeks",
                "ramp_base_km", "rebuild_mode", "basis"):
        assert key in vt, f"volume_targets missing {key}"


def test_prompt_renders_computed_run_volume(test_db_session):
    """The LLM is told the computed target, not the athlete's dead hours knob."""
    ctx = PeriodizationEngine().compute_context(test_db_session)
    text = _format_training_context(ctx)
    assert "THIS WEEK'S RUN VOLUME" in text
    assert str(ctx["volume_targets"]["run_km_target"]) in text
    assert "hard cap" in text
    assert "target hours" not in text  # C6: weekly_hours_target is dead


def test_persisted_plan_context_carries_volume_targets(client, test_db_session, monkeypatch):
    """finalize_plan_write refreshes _context on every write — the computed
    target must ride in the persisted plan for the gate (B1) to read."""
    fake_week = {
        "week_summary": {"focus": "f", "rationale": "r",
                         "expected_total_hours": 8.0, "expected_run_km": 99.0},
        "days": {
            day: {"summary": "s", "workouts": [
                {"sport": "running", "title": "Easy Run", "total_time": "40 min",
                 "steps": []}], "rationale": "r", "coach_note": "c"}
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
        },
    }
    monkeypatch.setattr(
        ResponseAgent, "generate_weekly_plan",
        lambda self, *a, **kw: dict(fake_week),
    )
    monkeypatch.setattr(
        ResponseAgent, "generate_weekly_review",
        lambda self, *a, **kw: None, raising=False,
    )

    response = client.get("/weekly-plan")
    assert response.status_code == 200, response.text

    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    plan = (
        test_db_session.query(WeeklyPlan)
        .filter(WeeklyPlan.week_start == week_start)
        .order_by(WeeklyPlan.id.desc())
        .first()
    )
    assert plan is not None
    expected = PeriodizationEngine().compute_context(test_db_session)["volume_targets"]
    stored = plan.plan_json["_context"]["volume_targets"]
    assert stored["run_km_target"] == expected["run_km_target"]
    assert stored["run_km_hard_cap"] == expected["run_km_hard_cap"]
