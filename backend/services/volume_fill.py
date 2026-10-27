"""
Volume Fill — the week reaches its run-km target, in Python.

The trim (volume_gate.apply_terminal_repairs) cuts a week that runs OVER its
budget; nothing lifted a week that ran UNDER. The LLM planned 45.8 km against
a 50 km target on 2026-10-27 and the gate let it through as advisory: the
target is Python's (periodization_engine), so a short week is the same bug as
a long one (CLAUDE.md: the LLM never decides volume). Alex: "there is a
difference between 45k and 50k".

Order, cheapest change first:
1. Lengthen easy runs in the window, shortest first, up to EASY_RUN_MAX_MIN.
   The main step grows; warm-up and cool-down stay.
2. Still short by a real run's worth: add an Easy Run on an open day — a rest
   day first, then a strength-only day (a double), never the long run's day,
   the day before it last. Only days the athlete's run_days allow.

Minutes are converted at the easy band's midpoint, which is the athlete's own
training pace (pace_model.apply_training_pace — CDMX), the same arithmetic the
distance stamp uses. The watch runs these workouts by time, so time at the
real pace is the honest km.

Only weekly generation and the mid-week replan fill (run_plan_write_pipeline
fill_run_km=True). Never adapt-today or triage — a lighter day there is the
point — and never the goal race week. The caller re-runs the constraint
enforcer and the stamps after a fill.
"""
import re

from backend.services.pace_enforcer import _band_seconds, classify_run_workout
from backend.services.plan_normalizer import VALID_DAYS, map_sport
from backend.services.volume_gate import (
    _injury_blocked_sports, _is_race_week, _window_workouts, parse_minutes, workout_km,
)

FILL_MIN_KM = 1.0          # shortfalls under this are rounding, not a defect
NEW_RUN_MIN_KM = 3.0       # below this, an added run isn't worth the session
EASY_RUN_MAX_MIN = 75      # lengthen an easy run no further than this
NEW_RUN_MIN_MIN = 30
WARM_COOL_MIN = 5          # each side of an added run's main block
DEFAULT_EASY_SEC_KM = 360.0  # no pace model: the gate's own 6:00/km estimate
FILL_NOTE = "weekly km target (system fill)"
_NOT_FILLABLE = re.compile(r"strid|opener|shakeout|sprint|pick-?up|surge", re.I)
_DAY_ABBR = {d: d[:3].lower() for d in VALID_DAYS}


def _fmt(minutes: int) -> str:
    """Minutes -> the plan's duration text: "55:00", "1:09:00"."""
    if minutes >= 60:
        return f"{minutes // 60}:{minutes % 60:02d}:00"
    return f"{minutes}:00"


def _easy_pace_min(pace_model) -> float:
    band = _band_seconds(pace_model, "easy")
    sec = (band[0] + band[1]) / 2 if band else DEFAULT_EASY_SEC_KM
    return sec / 60


def _fillable_easy(w: dict) -> bool:
    return (map_sport(w.get("sport") or "") == "running"
            and classify_run_workout(w.get("title")) == "easy"
            and not _NOT_FILLABLE.search(w.get("title") or ""))


MIN_MAIN_STEP_MIN = 5


def _resize(w: dict, delta: int) -> None:
    """Grow or shrink a run by `delta` minutes through its main block;
    warm-up and cool-down stay."""
    steps = [s for s in (w.get("steps") or []) if isinstance(s, dict)]
    main = next((s for s in steps if s.get("type") == "main"), None)
    if main is None and steps:
        main = max(steps, key=lambda s: parse_minutes(s.get("duration")) or 0)
    if main is not None:
        main_min = int(round(parse_minutes(main.get("duration")) or 0))
        main["duration"] = _fmt(max(MIN_MAIN_STEP_MIN, main_min + delta))
    total = int(round(parse_minutes(w.get("total_time")) or 0)) + delta
    w["total_time"] = _fmt(total)
    w.pop("distance_km", None)   # restamped from the new time


def _lengthen(w: dict, extra: int) -> None:
    _resize(w, extra)
    w["volume_fill"] = f"Lengthened +{extra} min: {FILL_NOTE}"


def _easy_hr_target(plan_json: dict) -> str:
    for _d, w in _window_workouts(plan_json, None):
        if _fillable_easy(w) and w.get("hr_target"):
            return w["hr_target"]
    return "Z2"


def _new_run(minutes: int, hr_target: str) -> dict:
    main = minutes - 2 * WARM_COOL_MIN
    return {
        "sport": "running",
        "title": "Easy Run",
        "steps": [
            {"type": "warmup", "duration": _fmt(WARM_COOL_MIN), "zone": 1,
             "description": "Gentle jog"},
            {"type": "main", "duration": _fmt(main), "zone": 2,
             "description": "Steady easy effort"},
            {"type": "cooldown", "duration": _fmt(WARM_COOL_MIN), "zone": 1,
             "description": "Very easy jog"},
        ],
        "total_time": _fmt(minutes),
        "hr_target": hr_target,
        "muscle_groups": [],
        "volume_fill": f"Added: {FILL_NOTE}",
    }


def _open_days(plan_json: dict, window, availability: dict, long_run_day: str) -> list:
    """Days an added run may go, best first: rest days, then strength-only
    days, the day before the long run last, the long run's day never."""
    allowed = {d.strip().lower() for d in (availability.get("run_days") or "").split(",")}
    eve = VALID_DAYS[(VALID_DAYS.index(long_run_day) - 1) % 7] if long_run_day in VALID_DAYS else None
    ranked = []
    for day_name in window:
        if day_name == long_run_day or _DAY_ABBR[day_name] not in allowed:
            continue
        day = (plan_json.get("days") or {}).get(day_name)
        if not isinstance(day, dict):
            continue
        sports = {map_sport(w.get("sport") or "") for w in day.get("workouts") or []
                  if isinstance(w, dict)}
        if sports - {"rest", "strength"}:
            continue   # already trains something with a heart rate
        rank = 0 if sports <= {"rest"} else 1
        ranked.append((day_name == eve, rank, VALID_DAYS.index(day_name), day_name))
    return [d for *_k, d in sorted(ranked)]


def fill_run_km(plan_json: dict, ctx: dict, days=None, completed_run_km: float = 0.0,
                availability: dict = None, active_injuries=None) -> tuple:
    """Lift the window's running to the week's target. Returns (plan_json,
    fills) — one {"day", "title", "minutes", "action"} per change."""
    vt = (ctx or {}).get("volume_targets") or {}
    target = vt.get("run_km_target")
    if not target or _is_race_week(ctx or {}):
        return plan_json, []
    if "running" in _injury_blocked_sports(active_injuries or []):
        return plan_json, []
    window = list(days) if days is not None else list(VALID_DAYS)
    planned = sum(workout_km(w)[0] for _d, w in _window_workouts(plan_json, window)
                  if map_sport(w.get("sport") or "") == "running")
    deficit = target - completed_run_km - planned
    if deficit < FILL_MIN_KM:
        return plan_json, []

    pace = _easy_pace_min((ctx or {}).get("pace_model"))
    fills = []

    easy = sorted(((d, w) for d, w in _window_workouts(plan_json, window) if _fillable_easy(w)),
                  key=lambda dw: parse_minutes(dw[1].get("total_time")) or 0)
    for day_name, w in easy:
        if deficit < FILL_MIN_KM:
            break
        room = EASY_RUN_MAX_MIN - int(round(parse_minutes(w.get("total_time")) or 0))
        extra = min(room, int(round(deficit * pace)))
        if extra < 5:
            continue
        _lengthen(w, extra)
        deficit -= extra / pace
        fills.append({"day": day_name, "title": w.get("title"), "minutes": extra,
                      "action": "lengthened"})

    long_run_day = (ctx or {}).get("long_run_day") or "Sunday"
    hr_target = _easy_hr_target(plan_json)
    for day_name in _open_days(plan_json, window, availability or {}, long_run_day):
        if deficit < NEW_RUN_MIN_KM:
            break
        minutes = int(min(EASY_RUN_MAX_MIN, max(NEW_RUN_MIN_MIN, round(deficit * pace))))
        day = plan_json["days"][day_name]
        kept = [w for w in day.get("workouts") or []
                if isinstance(w, dict) and map_sport(w.get("sport") or "") != "rest"]
        day["workouts"] = [_new_run(minutes, hr_target)] + kept
        if not kept:
            day["summary"] = "Easy run — added to reach the week's km target"
        deficit -= minutes / pace
        fills.append({"day": day_name, "title": "Easy Run", "minutes": minutes,
                      "action": "added"})
    return plan_json, fills


EASY_RUN_MIN_MIN = 30      # shorten an easy run no further than this
TRIM_NOTE = "weekly volume over budget (system trim)"


def shorten_easy_runs(plan_json: dict, excess_km: float, excess_hours: float,
                      days=None) -> tuple:
    """The trim's first move: shave minutes off the longest easy runs, down
    to EASY_RUN_MIN_MIN, before any session is deleted. 2026-10-27: the
    trim deleted two whole easy runs and the fill then stretched a third,
    leaving four runs for a 50 km week. Each run keeps its own km-per-minute
    (its stamp is already at the athlete's pace), so no pace model is
    needed. Returns (excess_km, excess_hours, repairs) left after shaving."""
    # The week's longest run is protected here as in the strip, whatever
    # its title.
    longest = max(((workout_km(w)[0], id(w)) for _d, w in _window_workouts(plan_json, None)
                   if map_sport(w.get("sport") or "") == "running"), default=(0, None))[1]
    runs = []
    for day_name, w in _window_workouts(plan_json, days):
        minutes = parse_minutes(w.get("total_time")) or 0
        km, _ = workout_km(w)
        if (_fillable_easy(w) and id(w) != longest
                and minutes > EASY_RUN_MIN_MIN and km > 0):
            runs.append({"day": day_name, "w": w, "min": int(round(minutes)),
                         "start": int(round(minutes)), "rate": km / minutes})
    # One minute at a time off the longest run: the cut spreads evenly from
    # the top instead of gutting one session.
    while (excess_km > 0 or excess_hours > 0) and runs:
        r = max(runs, key=lambda x: x["min"])
        if r["min"] <= EASY_RUN_MIN_MIN:
            break
        r["min"] -= 1
        excess_km -= r["rate"]
        excess_hours -= 1 / 60
    repairs = []
    for r in runs:
        cut = r["start"] - r["min"]
        if cut <= 0:
            continue
        w = r["w"]
        _resize(w, -cut)
        w["distance_km"] = round(r["min"] * r["rate"], 1)
        reason = f"Shortened −{cut} min: {TRIM_NOTE}"
        w["volume_trim"] = reason
        repairs.append({"day": r["day"], "title": w.get("title"), "reason": reason})
    return excess_km, excess_hours, repairs
