"""The 3:1 cycle must be constant across a plan week.

`training_start_date` is the earliest activity's date — an arbitrary weekday —
while plan weeks are Monday-start. Dividing elapsed days straight from it let
the cycle roll over mid-week: with a Wednesday anchor, Monday 2026-09-28 was
cycle week 4 (deload) and Wednesday 2026-09-30, the same plan week, was week 1.
Every plan write recomputes the context, so a deload regenerated mid-week
(travel rebuild, issue_triage apply, replan-remaining) silently became a build
week from Wednesday on: run target 0.75x -> 1.0x, quality cap 1 -> 2, and the
recovery banner gone, in the middle of the deload.
"""
from datetime import date, timedelta

from backend.services.periodization_engine import PeriodizationEngine


def _engine():
    return PeriodizationEngine.__new__(PeriodizationEngine)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def test_cycle_week_is_constant_across_every_plan_week():
    engine = _engine()
    for offset in range(7):                      # every anchor weekday
        anchor = date(2026, 9, 1) + timedelta(days=offset)
        for week in range(12):
            monday = _monday(date(2026, 9, 28)) + timedelta(weeks=week)
            values = {
                engine._get_cycle_week(anchor, monday + timedelta(days=n))["cycle_week"]
                for n in range(7)
            }
            assert len(values) == 1, (
                f"anchor {anchor} ({anchor.strftime('%a')}), week of {monday}: "
                f"cycle_week changed mid-week -> {sorted(values)}"
            )


def test_cycle_week_increments_by_one_per_week_and_wraps():
    engine = _engine()
    anchor = date(2026, 9, 2)                     # a Wednesday
    start = _monday(date(2026, 9, 7))
    seen = [
        engine._get_cycle_week(anchor, start + timedelta(weeks=w))["cycle_week"]
        for w in range(8)
    ]
    for prev, nxt in zip(seen, seen[1:]):
        assert nxt == (prev % 4) + 1, f"{seen} does not step 1,2,3,4,1,..."
    assert set(seen) == {1, 2, 3, 4}


def test_recovery_week_is_exactly_cycle_week_four():
    engine = _engine()
    anchor = date(2026, 9, 2)
    start = _monday(date(2026, 9, 7))
    for w in range(8):
        info = engine._get_cycle_week(anchor, start + timedelta(weeks=w))
        assert info["is_recovery_week"] == (info["cycle_week"] == 4)


def test_missing_training_start_date_degrades_to_build_week_one():
    info = _engine()._get_cycle_week(None, date(2026, 9, 28))
    assert info["cycle_week"] == 1
    assert info["is_recovery_week"] is False
