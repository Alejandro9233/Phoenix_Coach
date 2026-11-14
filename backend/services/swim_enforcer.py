"""
Swim enforcer — holds cross-training swims to the shape the engine budgeted.

WHY THIS EXISTS: a marathon goal budgets two easy 30-minute technique swims
a week, on top of the run plan (Alex, 2026-11-14: he got a pool, and the swim
takes the walk's place in the fat-loss deficit while he learns to swim). The
budget reaches the LLM as one line of prose, and a constraint that lives only
in a prompt is a suggestion: left alone it writes a 60-minute CSS set, or a
swim on every open day. At 2,240 m with a low HRV that is recovery the
marathon needs.

The rule comes from the training context: a `sport_sessions` entry marked
`on_top` (periodization_engine) with its `sessions` and `hours_each`. No such
entry — a triathlon goal, or swimming switched off in the profile — and this
module does nothing; triathlon swims are real training and keep their shape.

What it does, in order, to the days being written:
  1. Over the weekly count (zero in race week): the extra swims are removed,
     earliest kept. Swims on days outside `days` count but are never touched.
  2. No rest day left in the week: a swim that is the only session of its
     day is removed, latest first, until one day is free.
  3. Every swim left is titled "Technique Session", and one that runs long
     or carries a hard step is rewritten to the easy session.

Runs after constraint_enforcer (availability and injuries decide first) and
before the stamps and the gate, so both judge the final week.
"""
from typing import Iterable, Optional

from backend.services.constraint_enforcer import _rest_workout
from backend.services.plan_normalizer import VALID_DAYS, map_sport
from backend.services.volume_gate import is_quality, parse_minutes

TITLE = "Technique Session"


def swim_rule(ctx: Optional[dict]) -> Optional[dict]:
    """{"max": swims a week, "minutes": each} from a training context, or
    None when the context budgets no on-top swims."""
    info = (((ctx or {}).get("volume_references") or {}).get("sport_sessions") or {}).get("swimming")
    if not (isinstance(info, dict) and info.get("on_top")):
        return None
    return {"max": int(info.get("sessions") or 0),
            "minutes": int(round(float(info.get("hours_each") or 0.5) * 60))}


def _is_swim(w) -> bool:
    return isinstance(w, dict) and map_sport(w.get("sport") or "") == "swimming"


def _is_rest_day(day) -> bool:
    workouts = (day or {}).get("workouts") or []
    return all(not isinstance(w, dict) or map_sport(w.get("sport") or "") == "rest"
               for w in workouts)


def _easy_steps(minutes: int) -> list:
    return [
        {"type": "warmup", "duration": "05:00", "zone": 1,
         "description": "Easy laps, any stroke, rest at the wall when you need it"},
        {"type": "main", "duration": f"{minutes - 10:02d}:00", "zone": 2,
         "description": "Technique drills and relaxed laps. Rest between lengths; never out of breath"},
        {"type": "cooldown", "duration": "05:00", "zone": 1,
         "description": "Easy laps"},
    ]


def _remove(day_name: str, day: dict, workout: dict, reason: str, violations: list) -> None:
    violations.append({"day": day_name, "sport": "swimming",
                       "title": workout.get("title") or "Swim", "reason": reason})
    kept = [w for w in day.get("workouts") or [] if w is not workout]
    if not kept:
        kept = [_rest_workout(reason)]
        day["summary"] = "Rest — scheduled session removed"
    day["workouts"] = kept


def enforce_swims(plan_json: dict, rule: Optional[dict],
                  days: Optional[Iterable[str]] = None) -> tuple:
    """Returns `(plan_json, violations)`; violations are the swims removed,
    in the constraint enforcer's shape. Mutates plan_json in place."""
    if not rule:
        return plan_json, []
    target = set(days) if days is not None else set(VALID_DAYS)
    days_dict = plan_json.get("days") or {}
    violations = []

    def swims():
        return [(d, w) for d in VALID_DAYS if isinstance(days_dict.get(d), dict)
                for w in days_dict[d].get("workouts") or [] if _is_swim(w)]

    over = len(swims()) - rule["max"]
    reason = ("No swim in race week" if rule["max"] == 0
              else f"Only {rule['max']} easy swims a week while training for the race")
    for d, w in reversed(swims()):
        if over <= 0:
            break
        if d in target:
            _remove(d, days_dict[d], w, reason, violations)
            over -= 1

    week = [days_dict.get(d) for d in VALID_DAYS]
    if len([d for d in week if isinstance(d, dict)]) == 7 and not any(_is_rest_day(d) for d in week):
        for d, w in reversed(swims()):
            if d in target and len(days_dict[d].get("workouts") or []) == 1:
                _remove(d, days_dict[d], w, "The week keeps one full rest day", violations)
                break

    for d, w in swims():
        if d not in target:
            continue
        w["title"] = TITLE
        minutes = parse_minutes(w.get("total_time"))
        if minutes is None or minutes > rule["minutes"] or is_quality(w):
            km = w.get("distance_km")
            if isinstance(km, (int, float)) and minutes and minutes > rule["minutes"]:
                w["distance_km"] = round(km * rule["minutes"] / minutes, 1)
            w["steps"] = _easy_steps(rule["minutes"])
            w["total_time"] = f"{rule['minutes']} min"
    return plan_json, violations
