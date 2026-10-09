"""Weekly volume by sport — the Recent tab's volume ledger.

Reader 2 of docs/COROS_MCP.md: hours per sport per week, so the gap the
periodization engine cannot see shows up where Alex looks back. Cycling went
to zero after the CDMX move with nothing recorded in its place; swimming
months earlier. The engine counts what happened, not what stopped.

Python = GPS: the week buckets, the four-week average and the "gone" list
are computed here, once, from activities.duration_sec. iOS draws them. The
LLM is not involved.

Shape (hours rounded to 0.1, weeks oldest → newest, the last one is the
current, partial week):
  {"weeks": [{"start": "2026-08-17", "hours": {"run": 3.2, ...}, "total": 8.7}, ...],
   "sports": ["run", "strength", "bike", "swim"] (+ "other" only when non-zero),
   "this_week": {"run": 1.6, ..., "total": 4.4},
   "avg_4wk": {...}, "delta_4wk": {...},           # the four weeks before this one
   "gone": [{"sport": "bike", "weeks": 3, "was": 2.2}]}
None when the window holds no training at all.
"""
from datetime import date, datetime, time, timedelta

from backend.models.database import Activity
from backend.utils.timezone import get_local_today

WEEKS = 8            # the window the ledger draws
AVERAGE_WEEKS = 4    # "vs 4 wk": the four full weeks before the current one
GONE_QUIET_WEEKS = 2  # a sport with hours in the window but none in the last two is "gone"
SPORTS = ("run", "strength", "bike", "swim")   # the ledger's fixed rows, display order

_SPORT_KEYS = {
    "running": "run", "run": "run", "trail_running": "run", "treadmill": "run",
    "cycling": "bike", "bike": "bike", "ride": "bike", "indoor_cycling": "bike",
    "swimming": "swim", "swim": "swim", "open_water_swimming": "swim", "pool": "swim",
    "strength": "strength", "gym": "strength", "training": "strength",
}


def sport_key(sport) -> str:
    """activities.sport → ledger row. Anything unknown is "other"."""
    return _SPORT_KEYS.get((sport or "").lower().strip().replace(" ", "_"), "other")


def week_start(d: date) -> date:
    """Monday, like the plan's week_start."""
    return d - timedelta(days=d.weekday())


def gone_sports(weeks: list, sports: list) -> list:
    """Sports with hours somewhere in the window and none in the last
    GONE_QUIET_WEEKS. `was` is the mean of up to AVERAGE_WEEKS weeks ending
    at the last week with hours — what a normal week looked like."""
    out = []
    for sport in sports:
        series = [w["hours"].get(sport, 0.0) for w in weeks]
        if not any(series) or any(series[-GONE_QUIET_WEEKS:]):
            continue
        last = max(i for i, h in enumerate(series) if h)
        before = series[max(0, last - AVERAGE_WEEKS + 1):last + 1]
        out.append({"sport": sport, "weeks": len(series) - 1 - last,
                    "was": round(sum(before) / len(before), 1)})
    return out


def weekly_volume(db, today: date | None = None, weeks: int = WEEKS) -> dict | None:
    today = today or get_local_today()
    this_start = week_start(today)
    first = this_start - timedelta(weeks=weeks - 1)
    acts = db.query(Activity).filter(
        Activity.start_time >= datetime.combine(first, time.min)).all()

    buckets = {first + timedelta(weeks=i): {} for i in range(weeks)}
    for a in acts:
        if not a.start_time or not a.duration_sec:
            continue
        ws = week_start(a.start_time.date())
        if ws not in buckets:        # a row dated past today
            continue
        key = sport_key(a.sport)
        buckets[ws][key] = buckets[ws].get(key, 0.0) + a.duration_sec / 3600

    sports = list(SPORTS)
    if any(b.get("other") for b in buckets.values()):
        sports.append("other")
    rows = []
    for ws in sorted(buckets):
        rows.append({"start": ws.isoformat(),
                     "hours": {s: round(buckets[ws].get(s, 0.0), 1) for s in sports},
                     "total": round(sum(buckets[ws].values()), 1)})
    if not any(r["total"] for r in rows):
        return None

    this_week = rows[-1]
    previous = rows[-1 - AVERAGE_WEEKS:-1]
    avg = {s: round(sum(w["hours"][s] for w in previous) / len(previous), 1) if previous else 0.0
           for s in sports}
    return {
        "weeks": rows,
        "sports": sports,
        "this_week": {**this_week["hours"], "total": this_week["total"]},
        "avg_4wk": avg,
        "delta_4wk": {s: round(this_week["hours"][s] - avg[s], 1) for s in sports},
        "gone": gone_sports(rows, sports),
    }
