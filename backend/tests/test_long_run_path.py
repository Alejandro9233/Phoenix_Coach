"""The marathon long run reaches its peak before the taper.

Rules locked in here:
- In Build and Peak the long run climbs a geometric path to the profile's
  peak_long_run_km (27 km) by the last week before the taper, re-solved every
  week from the longest run of the last 3 weeks. It is a floor under the
  +12 min rule and outranks the phase's long-run cap (27 km at 6:00/km is
  162 min, over Peak's 150). After the 2026-09 comeback the +12 min rule
  alone reached ~18 km.
- Minutes come from the median pace of the window's runs, so the Nov 8
  half-marathon trial's race pace can't shrink the 27 km after it.
- No path in a comeback (the single-run cap rules there), a recovery week,
  outside Build and Peak, or once the peak has been run.
- compute_context passes the real phase id. It passed phase_info.get("id"),
  always None, so the taper never stepped volume down (found 2026-09-29).
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import _format_training_context
from backend.models.database import Activity, Athlete, Base
from backend.services.periodization_engine import (
    DISTANCE_PROFILES, TAPER_LONG_RUN_MIN, TAPER_RUN_RETAIN, PeriodizationEngine,
)
from backend.utils.timezone import get_local_today

MONDAY = date(2026, 10, 19)          # the first week after Alex's comeback
MARATHON = DISTANCE_PROFILES["Marathon"]
PEAK_KM = MARATHON["peak_long_run_km"]
COMEBACK = {"active": True, "step": 2, "steps": 3, "ladder": (0.65, 0.80, 1.00),
            "target_km": 38.7, "base_km": 38.7, "break_weeks": 3}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _run(db, day, km, pace=6.0):
    db.add(Activity(
        id=str(uuid.uuid4()),
        sport="running",
        start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
        distance_m=km * 1000,
        duration_sec=km * pace * 60,
        source="test",
    ))


def _week(db, monday, long_km=None, long_pace=6.0):
    """Three 6 km easy runs, and the long run on Sunday."""
    for day in range(3):
        _run(db, monday + timedelta(days=day), 6)
    if long_km:
        _run(db, monday + timedelta(days=6), long_km, long_pace)
    db.commit()


def _window(db, long_km, long_pace=6.0, monday=MONDAY):
    """The 3 weeks before `monday`; the longest run is the last Sunday."""
    for n in (3, 2):
        _week(db, monday - timedelta(weeks=n))
    _week(db, monday - timedelta(weeks=1), long_km, long_pace)


def _target(db, phase="peak", weeks_to_peak=0, monday=MONDAY, recovery=False,
            comeback=None, peak_km=PEAK_KM):
    phase_def = PeriodizationEngine._phase_def(MARATHON, {"phase": phase})
    return PeriodizationEngine()._get_weekly_run_target(
        db, phase_def, recovery, monday + timedelta(days=1),
        phase_id=phase, weeks_to_race=weeks_to_peak + 4, comeback=comeback,
        peak_long_run_km=peak_km, weeks_to_peak=weeks_to_peak)


def test_first_week_after_the_comeback_starts_the_climb(db):
    _window(db, 9.7)                      # Oct 18: the capped comeback long run
    vt = _target(db, "build", weeks_to_peak=3)
    assert vt["long_run_path"] == {"km": 12.5, "peak_km": 27.0, "weeks_to_peak": 3}
    assert vt["long_run_minutes"] == 75   # 12.5 km at 6:00, not 58 + 12 = 70


def test_the_path_passes_the_half_and_lands_on_27km(db):
    _window(db, 9.7)
    planned = []
    for k, phase in enumerate(("build", "peak", "peak", "peak")):
        monday = MONDAY + timedelta(weeks=k)
        path = _target(db, phase, weeks_to_peak=3 - k, monday=monday)["long_run_path"]
        planned.append(path["km"])
        _week(db, monday, path["km"])     # run as planned
    # Nov 8 is ~21 km: the half-marathon trial is that week's long run.
    assert planned == [12.5, 16.2, 20.9, 27.0]


def test_the_week_after_the_half_plans_27km_at_easy_pace(db):
    _window(db, 21.1, long_pace=4.6)      # the trial, raced in ~97 min
    vt = _target(db, "peak", weeks_to_peak=0)
    assert vt["long_run_path"]["km"] == 27.0
    assert vt["long_run_minutes"] == 162  # at the 6:00 median, not the 4:36 race pace
    peak_def = PeriodizationEngine._phase_def(MARATHON, {"phase": "peak"})
    assert vt["long_run_minutes"] > peak_def["long_run_cap_min"]


def test_a_long_build_keeps_the_12_minute_step(db):
    _window(db, 16)                       # 96 min
    vt = _target(db, "build", weeks_to_peak=10)
    assert vt["long_run_path"] is None
    assert vt["long_run_minutes"] == 108


@pytest.mark.parametrize("kw", [
    {"comeback": COMEBACK},
    {"recovery": True},
    {"phase": "base", "weeks_to_peak": 3},
    {"weeks_to_peak": -1},
    {"peak_km": None},
])
def test_no_path_in_a_comeback_a_deload_or_outside_build_and_peak(db, kw):
    _window(db, 9.7)
    assert _target(db, **kw)["long_run_path"] is None


def test_no_path_once_the_peak_has_been_run(db):
    _window(db, 28)
    vt = _target(db, "peak", weeks_to_peak=0)
    assert vt["long_run_path"] is None
    assert vt["long_run_minutes"] == 150  # the Peak cap, as before


def _seed_athlete(db, weeks_to_race, history):
    today = get_local_today()
    monday = today - timedelta(days=today.weekday())
    db.add(Athlete(
        name="Test", race_date=today + timedelta(weeks=weeks_to_race, days=3),
        race_distance="Marathon",
        run_days="mon,tue,wed,thu,fri,sat,sun", swim_days="", bike_days="",
        strength_days="mon,wed,fri", training_start_date=monday,
    ))
    db.commit()
    history(monday)


def test_context_plans_the_path_seven_weeks_out(db):
    _seed_athlete(db, 7, lambda monday: _window(db, 9.7, monday=monday))
    ctx = PeriodizationEngine().compute_context(db)
    assert ctx["volume_targets"]["long_run_path"] == {
        "km": 12.5, "peak_km": 27.0, "weeks_to_peak": 3}
    assert "Long run: 12.5 km (~75 min)" in _format_training_context(ctx)


def test_context_steps_the_taper_down(db):
    def fifty_km_weeks(monday):
        for n in (3, 2, 1):
            for day in range(5):
                _run(db, monday - timedelta(weeks=n, days=-day), 10)
        db.commit()
    _seed_athlete(db, 2, fifty_km_weeks)
    vt = PeriodizationEngine().compute_context(db)["volume_targets"]
    assert vt["run_km_target"] == 50 * TAPER_RUN_RETAIN[2]   # was 50: no taper
    assert vt["long_run_minutes"] <= TAPER_LONG_RUN_MIN[2]
