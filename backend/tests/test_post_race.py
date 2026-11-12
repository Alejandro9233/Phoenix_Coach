"""After the goal race: a 3-week reverse taper, then Foundation (2026-11-12).

Rules locked in here:
- Before this the engine clamped weeks_to_race to 0 and kept the taper
  forever after race day.
- The three weeks after race week run 20% / 45% / 60% of the best week of
  the 4 before race week, easy only: no quality, no deload, and no comeback
  ladder even when the first week is short enough to read as a break.
- Then Foundation (weeks_to_race 99) until a new race date is set, with the
  3:1 cycle restarting where the recovery ends.
- Race Sunday itself is still race week.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest

from backend.agents.response_agent import _format_training_context, _journey
from backend.models.database import Activity, Athlete
from backend.services import periodization_engine
from backend.services.periodization_engine import PeriodizationEngine
from backend.tests.test_long_run_path import db  # noqa: F401

RACE = date(2026, 12, 13)          # a Sunday
RACE_MONDAY = date(2026, 12, 7)
PRE_RACE = {date(2026, 11, 9): 55, date(2026, 11, 16): 60,
            date(2026, 11, 23): 55, date(2026, 11, 30): 38}


def _week(db, monday, km):
    for d in range(7):
        db.add(Activity(
            id=str(uuid.uuid4()), sport="running", source="test",
            start_time=datetime.combine(monday + timedelta(days=d), datetime.min.time())
            + timedelta(hours=7),
            distance_m=km / 7 * 1000, duration_sec=km / 7 * 6 * 60,
        ))


def _seed(db, after=()):
    """The pre-race block, race week (shakeouts + the marathon) and `after`,
    the km of each week since the race."""
    db.add(Athlete(name="T", race_date=RACE, race_distance="Marathon",
                   training_start_date=date(2026, 9, 1)))
    for monday, km in PRE_RACE.items():
        _week(db, monday, km)
    _week(db, RACE_MONDAY, 8)
    db.add(Activity(id=str(uuid.uuid4()), sport="running", source="test",
                    start_time=datetime.combine(RACE, datetime.min.time())
                    + timedelta(hours=7),
                    distance_m=42195, duration_sec=200 * 60))
    for n, km in enumerate(after, start=1):
        _week(db, RACE_MONDAY + timedelta(weeks=n), km)
    db.commit()


def _ctx(db, monkeypatch, today):
    monkeypatch.setattr(periodization_engine, "get_local_today", lambda: today)
    return PeriodizationEngine().compute_context(db)


@pytest.mark.parametrize("week, after, km, long_min", [
    (1, (), 12.0, 40),
    # A 3 km first week would read as a break; the ladder must not take over.
    (2, (3,), 27.0, 60),
    (3, (3, 27), 36.0, 75),
])
def test_three_easy_weeks_off_the_pre_race_peak(db, monkeypatch, week, after, km, long_min):
    _seed(db, after)
    ctx = _ctx(db, monkeypatch, RACE_MONDAY + timedelta(weeks=week, days=1))
    vt = ctx["volume_targets"]
    assert ctx["phase"] == "post_race"
    assert ctx["post_race_week"] == week and ctx["race_done"]
    assert not ctx["race_week"]
    assert vt["run_km_target"] == km
    assert vt["run_km_hard_cap"] == pytest.approx(km * 1.05, abs=0.1)
    assert vt["long_run_minutes"] == long_min
    assert vt["comeback"] is None
    assert ctx["volume_references"]["max_quality_sessions"] == 0
    assert not ctx["is_recovery_week"]
    assert "Tempo Run" in ctx["forbidden_workouts"]


def test_foundation_after_the_recovery(db, monkeypatch):
    _seed(db, (15, 27, 36))
    ctx = _ctx(db, monkeypatch, RACE_MONDAY + timedelta(weeks=4, days=1))
    assert ctx["phase"] == "foundation"
    assert ctx["race_done"] and ctx["post_race_week"] is None
    assert ctx["weeks_to_race"] == 99
    assert ctx["cycle_week"] == 1   # the cycle restarts where recovery ends
    assert ctx["volume_targets"]["long_run_path"] is None


def test_race_sunday_is_still_race_week(db, monkeypatch):
    _seed(db)
    ctx = _ctx(db, monkeypatch, RACE)
    assert ctx["phase"] == "taper" and ctx["race_week"]
    assert not ctx["race_done"]


def test_the_prompt_stops_counting_down(db, monkeypatch):
    _seed(db)
    ctx = _ctx(db, monkeypatch, RACE_MONDAY + timedelta(weeks=1, days=1))
    text = _format_training_context(ctx)
    assert "already run" in text and "Weeks to race" not in text
    assert "in 0 weeks" not in _journey("M", "Marathon", 0, True)
