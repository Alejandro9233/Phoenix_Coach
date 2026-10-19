"""
Long-run day — the week's long run lands on one fixed day, enforced.

Alex, 2026-10-19: "if we can fix the long run to be always on Sunday that'd
be great." The prompt says so, but a constraint that lives only in a prompt
is a suggestion (CLAUDE.md), so the write pipeline moves it: the day holding
the long run and the target day swap whole — workouts, summary, rationale,
coach note — so a strength session scheduled beside the long run travels
with it and nothing is dropped or invented. Correction, not stripping.

No-ops, in order: the target day is outside the days window (a replan that
doesn't own Sunday must not move it), the target day is not a run day in
availability (the enforcer already stripped runs there), the week has no
run, or the long run is already on the target day.

The long run is the running workout titled as one (pace_enforcer's
classification; the longest such if several); failing that, the longest run
by total_time.
"""
from backend.services.pace_enforcer import classify_run_workout
from backend.services.plan_normalizer import VALID_DAYS, map_sport
from backend.services.volume_gate import parse_minutes

LONG_RUN_DAY = "Sunday"
_DAY_ABBR = {d: d[:3].lower() for d in VALID_DAYS}


def _runs(day: dict):
    return [w for w in (day.get("workouts") or [])
            if isinstance(w, dict) and map_sport(w.get("sport") or "") == "running"]


def _minutes(w: dict) -> float:
    return parse_minutes(w.get("total_time")) or 0.0


def find_long_run_day(plan_json: dict, days=None):
    """The day name holding the week's long run inside the window, or None."""
    window = list(days) if days is not None else list(VALID_DAYS)
    candidates = []
    for day_name in window:
        day = (plan_json.get("days") or {}).get(day_name)
        if not isinstance(day, dict):
            continue
        for w in _runs(day):
            titled = classify_run_workout(w.get("title")) == "long_run"
            candidates.append((titled, _minutes(w), day_name))
    if not candidates:
        return None
    titled = [c for c in candidates if c[0]]
    pool = titled or candidates
    return max(pool, key=lambda c: c[1])[2]


def _rest_day(reason: str) -> dict:
    return {"summary": "Rest", "rationale": reason, "coach_note": "",
            "workouts": [{"sport": "rest", "title": "Rest", "total_time": "0 min", "steps": []}]}


def enforce_long_run_day(plan_json: dict, day: str = LONG_RUN_DAY,
                         availability: dict | None = None, days=None) -> tuple:
    """Move the long run to `day` by swapping whole days. Returns
    (plan_json, moves); moves is [] when nothing changed."""
    if not day or day not in VALID_DAYS:
        return plan_json, []
    window = list(days) if days is not None else list(VALID_DAYS)
    if day not in window:
        return plan_json, []
    run_days = (availability or {}).get("run_days")
    if run_days is not None:
        allowed = {s.strip().lower() for s in str(run_days).split(",") if s.strip()}
        if _DAY_ABBR[day] not in allowed:
            return plan_json, []
    src = find_long_run_day(plan_json, window)
    if src is None or src == day:
        return plan_json, []
    days_map = plan_json.setdefault("days", {})
    moved = days_map[src]
    title = next((w.get("title") for w in _runs(moved)), "Long Run")
    days_map[src] = days_map.get(day) or _rest_day(f"The long run moved to {day}.")
    days_map[day] = moved
    return plan_json, [{"title": title, "from": src, "to": day}]
