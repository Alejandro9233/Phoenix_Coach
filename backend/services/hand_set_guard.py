"""
Guard for hand-set weeks (hand_set_weeks.py) against LLM rebuilds.

A hand-set week is written in Python on purpose. Every path that asks the LLM
to rebuild days of it (travel, injury swap, recovery, replan-remaining) must
leave the days its trigger did not touch exactly as stored — the stored day
already carries any earlier adaptation. One rule, shared by all four paths:

  - `restrict_rebuild_days`: the days the LLM may rewrite shrink to the days
    the trigger actually affects (all of them, for an ordinary week).
  - `overlay_hand_set_days`: replan-remaining takes the days from
    hand_set_week() instead of the LLM, same as regeneration does.

The enforcer and stamps still run after either; this only narrows who writes.
"""
import copy
from datetime import date

from backend.services import hand_set_weeks


def is_hand_set(week_start: date) -> bool:
    return hand_set_weeks.hand_set_week(week_start) is not None


def restrict_rebuild_days(week_start: date, candidates: list, affected: list) -> list:
    """`candidates` unchanged for an LLM-written week; in a hand-set week only
    the ones also in `affected` (days the trigger displaces or forbids)."""
    if not is_hand_set(week_start):
        return list(candidates)
    keep = set(affected)
    return [d for d in candidates if d in keep]


def overlay_hand_set_days(plan_json: dict, week_start: date, days: list):
    """A copy of `plan_json` with `days` taken from the hand-set week, or None
    when the week is not hand-set. Revisions and other keys are kept."""
    hand_set = hand_set_weeks.hand_set_week(week_start)
    if not hand_set:
        return None
    merged = copy.deepcopy(plan_json)
    for day_name in days:
        if day_name in (hand_set.get("days") or {}):
            merged.setdefault("days", {})[day_name] = hand_set["days"][day_name]
    return merged
