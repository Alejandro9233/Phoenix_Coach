"""Weekly run km Alex set by hand (RUN_KM_PLAN, 2026-10-27).

Rules locked in here:
- A planned week of the Dec 13 race replaces the ramp target and its hard
  cap, even above the ramp's 15% ceiling over demonstrated volume.
- Weeks outside the plan, or another race, keep the ramp.
- A comeback still wins: a break resets the ladder, not the plan.
"""
from datetime import date, timedelta

from backend.services.periodization_engine import (
    RUN_KM_PLAN, RUN_KM_PLAN_CAP, RUN_KM_PLAN_RACE, PeriodizationEngine,
)
from backend.tests.test_long_run_path import COMEBACK, MARATHON, _window, db  # noqa: F401

PLANNED = date(2026, 10, 26)


def _target(db, monday=PLANNED, race_date=RUN_KM_PLAN_RACE, comeback=None):
    phase_def = PeriodizationEngine._phase_def(MARATHON, {"phase": "peak"})
    return PeriodizationEngine()._get_weekly_run_target(
        db, phase_def, False, monday + timedelta(days=1),
        phase_id="peak", weeks_to_race=6, comeback=comeback,
        peak_long_run_km=27.0, weeks_to_peak=2, race_date=race_date)


def test_the_planned_week_overrides_the_ramp(db):
    _window(db, 15, monday=PLANNED)   # ~33 km weeks: the ramp would give 40
    vt = _target(db)
    assert vt["run_km_target"] == RUN_KM_PLAN[PLANNED] == 50.0
    assert vt["run_km_hard_cap"] == round(50.0 * RUN_KM_PLAN_CAP, 1)
    assert "planned week" in vt["basis"]


def test_an_unplanned_week_or_another_race_keeps_the_ramp(db):
    _window(db, 15, monday=PLANNED)
    for vt in (_target(db, monday=PLANNED + timedelta(weeks=5)),
               _target(db, race_date=RUN_KM_PLAN_RACE + timedelta(days=7))):
        assert vt["run_km_target"] < 50.0
        assert "planned week" not in vt["basis"]


def test_a_comeback_outranks_the_plan(db):
    _window(db, 15, monday=PLANNED)
    vt = _target(db, comeback=COMEBACK)
    assert vt["run_km_target"] <= COMEBACK["target_km"]
