"""Comeback after a break: the engine remembers the normal week.

Rules locked in here:
- A break is a week at or under RUN_NOISE_FLOOR_KM. The normal week is the
  best of the 4 weeks before it, so the week an injury cut short can't
  lower it.
- The restart depends on the break: 70% after one week, 65% after two or
  three, 50% after four or more. One step per week, and only once the watch
  shows >= 90% of the current step was run. Before this, 1-2 weeks off
  planned ABOVE the pre-break week and 3+ weeks of zero fell back to the
  40 km phase floor (2026-09-28).
- While coming back: no deload, no quality runs, and no run more than 10%
  over the longest run of the last 30 days — the gate rejects it.
- The 3:1 cycle restarts where the comeback ends. No deload in the taper or
  the week before it (they used to stack).
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Activity, Athlete, Base
from backend.services.periodization_engine import DISTANCE_PROFILES, PeriodizationEngine
from backend.services.plan_normalizer import VALID_DAYS
from backend.services.volume_gate import audit_plan
from backend.utils.timezone import get_local_today

MONDAY = date(2026, 9, 28)           # the week being planned
TODAY = MONDAY + timedelta(days=1)   # a Tuesday
MARATHON = DISTANCE_PROFILES["Marathon"]
BUILD_DEF = PeriodizationEngine._phase_def(MARATHON, {"phase": "build"})
TAPER_DEF = PeriodizationEngine._phase_def(MARATHON, {"phase": "taper"})


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


def _run(db, day, km, minutes=None):
    db.add(Activity(
        id=str(uuid.uuid4()),
        sport="running",
        start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
        distance_m=km * 1000,
        duration_sec=(minutes or km * 6) * 60,
        source="test",
    ))


def _weeks(db, kms, last_monday=MONDAY - timedelta(weeks=1)):
    """kms[-1] is the week of last_monday, each earlier entry one week before
    it; None is a week without runs. A week's km is split into runs of at
    most 6 km, one per day."""
    for n, km in enumerate(reversed(kms)):
        monday = last_monday - timedelta(weeks=n)
        remaining, day = km or 0, 0
        while remaining > 0:
            chunk = min(6, remaining)
            _run(db, monday + timedelta(days=day % 7), chunk)
            remaining -= chunk
            day += 1
    db.commit()


def _target(db, phase_def=BUILD_DEF, recovery=False, **kw):
    return PeriodizationEngine()._get_weekly_run_target(
        db, phase_def, recovery, TODAY, **kw)


def _comeback(db):
    return PeriodizationEngine()._detect_comeback(db, MONDAY)


def test_ankle_sprain_comeback_restarts_at_65_percent(db):
    """The 2026-09 sprain, from the watch: normal weeks up to 38.7 km, the
    injury week cut to 24.4, two break weeks, a 10.15 km week back, and a
    7.24 km run on Monday. The ramp planned 11.7 km; the ladder plans 25.2."""
    _weeks(db, [17.4, 8.5, 38.7, 24.4, None, 1.86, 10.15])
    _run(db, MONDAY, 7.24, minutes=50)
    db.commit()

    cb = _comeback(db)
    assert cb["active"] is True
    assert cb["break_weeks"] == 2
    assert cb["base_km"] == 38.7   # the shortened injury week can't lower it
    assert cb["step"] == 0         # 10.15 km is under 90% of the first step

    t = _target(db)
    assert t["run_km_target"] == 25.2   # 65% of 38.7
    assert t["run_km_hard_cap"] == 25.2
    assert t["single_run_cap_km"] == 8.0  # 1.1 x Monday's 7.24 km
    assert t["longest_run_30d_km"] == 7.2
    assert t["comeback"] == {"step": 0, "steps": 3, "base_km": 38.7,
                             "target_km": 25.2, "break_weeks": 2}


def test_a_step_unlocks_only_once_it_is_run(db):
    _weeks(db, [38.7, 38.7, 38.7, 38.7, None, None, 10.2, 23.0])
    cb = _comeback(db)
    assert cb["step"] == 1            # 23 >= 90% of 25.2; 10.2 was not
    assert cb["target_km"] == 31.0    # 80% of 38.7


@pytest.mark.parametrize("weeks_off,expected", [(1, 21.0), (2, 19.5), (3, 19.5), (5, 15.0)])
def test_the_restart_depends_on_how_long_the_break_was(db, weeks_off, expected):
    """30 km normal weeks, a break, then the first week back. The ramp used
    to plan 34.5 km after 1-2 weeks off and the 40 km phase floor after 3+."""
    _weeks(db, [30, 30, 30, 30] + [None] * weeks_off)
    assert _target(db)["run_km_target"] == expected


def test_no_deload_inside_a_comeback(db):
    _weeks(db, [30, 30, 30, 30, None, None, 10])
    assert _target(db, recovery=True)["run_km_target"] == 19.5


def test_first_week_back_with_no_recent_run_starts_small(db):
    """Five weeks off: nothing in the 3-week window, nothing in 30 days."""
    _weeks(db, [30, 30, 30, 30, None, None, None, None, None])
    t = _target(db)
    assert t["long_run_minutes"] == 30
    assert t["single_run_cap_km"] == 5.0


def test_comeback_ends_on_the_top_step_and_restarts_the_cycle(db):
    _weeks(db, [30, 30, 30, 30, None, None, 20, 25, 30])
    cb = _comeback(db)
    assert cb["active"] is False
    assert cb["ended"] == MONDAY  # the 30 km week completed the top step
    athlete = Athlete(training_start_date=date(2026, 7, 23))
    assert PeriodizationEngine._cycle_anchor(athlete, cb) == MONDAY
    assert _target(db)["comeback"] is None
    assert _target(db)["single_run_cap_km"] is None


def test_a_comeback_that_never_reaches_the_base_expires(db):
    """Eight weeks stuck under the first step: that volume is the new normal."""
    _weeks(db, [40, 40, 40, 40, None, None] + [22] * 8)
    assert _comeback(db)["active"] is False


def test_no_break_no_comeback(db):
    _weeks(db, [30, 32, 34])
    assert _comeback(db) is None
    assert _target(db)["comeback"] is None


def test_no_deload_in_the_taper_or_the_week_before_it():
    engine = PeriodizationEngine()

    def applies(weeks_to_race, race_week=False, comeback=False):
        return engine._deload_applies(True, race_week, comeback, weeks_to_race, "Marathon")

    assert applies(8) is True     # build
    assert applies(5) is True     # peak
    assert applies(4) is False    # last full week before the taper
    assert applies(3) is False    # taper
    assert applies(1) is False
    assert applies(0, race_week=True) is False
    assert applies(8, comeback=True) is False


def test_taper_and_recovery_week_do_not_stack(db):
    _weeks(db, [40, 44, 48])
    plain = _target(db, TAPER_DEF, phase_id="taper", weeks_to_race=1)
    both = _target(db, TAPER_DEF, recovery=True, phase_id="taper", weeks_to_race=1)
    assert both["run_km_target"] == plain["run_km_target"] == 21.6  # 45% of 48


def _plan(run_km_by_day):
    days = {}
    for day in VALID_DAYS:
        km = run_km_by_day.get(day)
        workouts = ([{"sport": "running", "title": "Easy Run", "steps": [],
                      "total_time": f"{round(km * 6.5)} min", "distance_km": km}]
                    if km else
                    [{"sport": "rest", "title": "Rest", "steps": [], "total_time": "0:00"}])
        days[day] = {"summary": "s", "rationale": "r", "coach_note": "c",
                     "workouts": workouts}
    return {"week_summary": {"focus": "f", "rationale": "r"}, "days": days}


def _comeback_ctx(cap=8.0):
    return {
        "phase": "build",
        "phase_name": "Marathon Build",
        "is_recovery_week": False,
        "recovery": {"status": "green"},
        "volume_targets": {"run_km_target": 25.2, "run_km_hard_cap": 25.2,
                           "single_run_cap_km": cap, "longest_run_30d_km": 7.2},
        "volume_references": {"phase_hours_range": "4-6", "max_quality_sessions": 0,
                              "sport_sessions": {"running": {"sessions": 5}}},
        "workout_menu": {"running": ["Easy Run", "Long Run (Z1-Z2 only)",
                                     "Strides/Openers"]},
        "forbidden_workouts": [],
    }


def test_gate_rejects_a_run_over_the_comeback_cap():
    avail = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}
    over = audit_plan(_plan({"Monday": 7.2, "Wednesday": 6.0, "Friday": 3.0,
                             "Saturday": 9.0}), _comeback_ctx(), availability=avail)
    hits = [v for v in over.hard if v["kind"] == "single_run_cap"]
    assert [(v["day"], v["run_km"]) for v in hits] == [("Saturday", 9.0)]
    assert "8.0 km" in hits[0]["detail"]

    ok = audit_plan(_plan({"Monday": 7.2, "Wednesday": 6.0, "Friday": 4.0,
                           "Saturday": 8.0}), _comeback_ctx(), availability=avail)
    assert not [v for v in ok.hard if v["kind"] == "single_run_cap"]


def test_gate_skips_the_cap_outside_a_comeback():
    avail = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}
    report = audit_plan(_plan({"Saturday": 9.0}), _comeback_ctx(cap=None),
                        availability=avail)
    assert not [v for v in report.hard if v["kind"] == "single_run_cap"]


def _seed_comeback_athlete(db):
    """A comeback on a week the calendar marks as a deload (cycle week 4)."""
    today = get_local_today()
    monday = today - timedelta(days=today.weekday())
    db.add(Athlete(
        name="Test", race_date=today + timedelta(weeks=10), race_distance="Marathon",
        run_days="mon,tue,wed,thu,fri,sat,sun", swim_days="", bike_days="",
        strength_days="mon,wed,fri", training_start_date=monday - timedelta(weeks=3),
    ))
    db.commit()
    _weeks(db, [38.7, 38.7, 38.7, 38.7, None, None, 10.2],
           last_monday=monday - timedelta(weeks=1))
    return monday


def test_context_during_a_comeback(db):
    _seed_comeback_athlete(db)
    ctx = PeriodizationEngine().compute_context(db)
    assert ctx["volume_targets"]["comeback"]["step"] == 0
    assert ctx["cycle_week"] == 4
    assert ctx["is_recovery_week"] is False
    assert ctx["recovery_note"].startswith("Comeback step 1 of 3")
    assert ctx["volume_references"]["max_quality_sessions"] == 0
    assert "Tempo Run" not in ctx["workout_menu"]["running"]
    assert "Tempo Run" in ctx["forbidden_workouts"]


def test_block_calendar_projects_the_comeback(db):
    monday = _seed_comeback_athlete(db)
    weeks = {w["week_start"]: w for w in PeriodizationEngine().compute_block_calendar(db)["weeks"]}
    this_week = weeks[monday.isoformat()]
    next_week = weeks[(monday + timedelta(weeks=1)).isoformat()]
    assert this_week["is_recovery_week"] is False
    assert this_week["expected_run_km"] == "25.2"
    assert next_week["expected_run_km"] == "31"
