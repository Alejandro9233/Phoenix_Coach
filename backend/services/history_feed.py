"""
The History feed — one merged, newest-first ledger of what the system did.

Two event types, one list:
- "refresh" rows come straight from refresh_events.payload_json (frozen at
  sync time, self-contained).
- "plan_change" rows are DERIVED on read from each week's plan_json
  ["_revisions"] — receipts are already durable and capped; a second copy
  would be a divergence bug waiting to happen.

FOLD RULE: a refresh that auto-adapted carries the exact `at` of the
adapt_today receipt it caused (adaptation.receipt_at). That receipt is folded
INTO the refresh row (adaptation.receipt) instead of appearing twice. An
unmatched receipt degrades to two adjacent rows — cosmetic, never lossy.

ORDERING: by UTC instant. Receipt timestamps are athlete-local ISO strings
WITH offset (get_local_now is zone-aware), refresh rows store UTC created_at
— both parse to aware datetimes, so travel never reorders the feed. Rows are
GROUPED for display by the local_day frozen at write time.

Legacy receipts predate the `after` snapshot; their after is reconstructed
from the next-newer receipt's `before` (per day) or the live plan, and
labeled after_source="reconstructed"/"current" so the diff sheet can say so.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from backend.models.database import RefreshEvent, WeeklyPlan
from backend.utils.timezone import get_timezone_name

DEFAULT_LIMIT = 30
MAX_LIMIT = 100


def _as_utc(dt_or_str) -> datetime | None:
    """Aware UTC datetime from a receipt `at` string or a DB datetime."""
    if dt_or_str is None:
        return None
    if isinstance(dt_or_str, datetime):
        dt = dt_or_str
    else:
        try:
            dt = datetime.fromisoformat(str(dt_or_str))
        except ValueError:
            return None
    if dt.tzinfo is None:
        # Receipts have carried an offset since they exist; DB created_at is
        # naive UTC. Strings without offset are treated as athlete-local.
        if isinstance(dt_or_str, datetime):
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.replace(tzinfo=ZoneInfo(get_timezone_name()))
    return dt.astimezone(timezone.utc)


def _newest_row_per_week(db):
    """WeeklyPlan rows, newest week first, newest row per week (the row every
    reader treats as that week's plan)."""
    rows = db.query(WeeklyPlan).order_by(
        WeeklyPlan.week_start.desc(), WeeklyPlan.id.desc()
    ).all()
    seen = set()
    for row in rows:
        if row.week_start in seen:
            continue
        seen.add(row.week_start)
        yield row


def _receipt_after(receipts, idx, plan_json) -> tuple:
    """(after, after_source) for receipts[idx]. Modern receipts carry `after`
    written at finalize time; legacy ones reconstruct per day from the
    next-newer receipt's `before`, else the live plan."""
    entry = receipts[idx]
    if entry.get("after") is not None:
        return entry["after"], "receipt"
    after = {}
    source = "current"
    for day in entry.get("days") or []:
        found = None
        for newer in receipts[idx + 1:]:
            if (newer.get("before") or {}).get(day) is not None:
                found = newer["before"][day]
                source = "reconstructed"
                break
        if found is None:
            day_plan = (plan_json.get("days") or {}).get(day) or {}
            found = [
                {"sport": w.get("sport"), "title": w.get("title"),
                 "total_time": w.get("total_time")}
                for w in day_plan.get("workouts") or []
                if isinstance(w, dict)
            ]
        after[day] = found
    return (after or None), source


def _local_day(at_utc: datetime, at_str) -> str:
    """Display grouping day. Receipts carry local time in their `at` string —
    the date part IS the frozen local day."""
    try:
        return str(at_str)[:10]
    except Exception:
        return at_utc.date().isoformat()


def build_history_feed(db, limit: int = DEFAULT_LIMIT, before: str = None) -> dict:
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    cursor = _as_utc(before)

    # Refresh events page (already newest-first, self-contained payloads).
    q = db.query(RefreshEvent)
    if cursor is not None:
        q = q.filter(RefreshEvent.created_at < cursor.replace(tzinfo=None))
    refresh_rows = q.order_by(
        RefreshEvent.created_at.desc(), RefreshEvent.id.desc()
    ).limit(limit).all()

    fold_keys = {}
    candidates = []
    for row in refresh_rows:
        payload = dict(row.payload_json or {})
        payload.setdefault("type", "refresh")
        payload["id"] = f"refresh:{row.id}"
        at_utc = _as_utc(row.created_at)
        payload["_sort"] = at_utc
        payload["local_day"] = payload.get("local_day") or row.local_day
        adaptation = payload.get("adaptation") or {}
        if adaptation.get("receipt_at") and adaptation.get("week_start"):
            fold_keys[(adaptation["week_start"], adaptation["receipt_at"])] = payload
        candidates.append(payload)

    # Plan receipts, walked newest-week-first until the page can't need more.
    for row in _newest_row_per_week(db):
        plan_json = row.plan_json or {}
        receipts = list(plan_json.get("_revisions") or [])
        week_start = row.week_start.isoformat() if row.week_start else None
        for idx in range(len(receipts) - 1, -1, -1):
            entry = receipts[idx]
            at_utc = _as_utc(entry.get("at"))
            if at_utc is None:
                continue
            if cursor is not None and at_utc >= cursor:
                continue
            # Fold only the adapt receipt itself — receipts share seconds
            # precision, so a generate written in the same second must not
            # match the join key.
            folded_into = (
                fold_keys.get((week_start, entry.get("at")))
                if entry.get("source") == "adapt_today" else None
            )
            if folded_into is not None:
                # Rendered inside the refresh row's detail, not as its own row.
                folded_into.setdefault("adaptation", {})["receipt"] = entry
                continue
            after, after_source = _receipt_after(receipts, idx, plan_json)
            candidates.append({
                "id": f"plan:{week_start}:{idx}",
                "type": "plan_change",
                "at": entry.get("at"),
                "local_day": _local_day(at_utc, entry.get("at")),
                "week_start": week_start,
                "source": entry.get("source"),
                "days": entry.get("days") or [],
                "reason": entry.get("reason"),
                "before": entry.get("before"),
                "after": after,
                "after_source": after_source,
                "stripped": entry.get("stripped") or [],
                "_sort": at_utc,
            })
        # Enough receipts older than the oldest refresh row to fill the page —
        # anything in yet-older weeks can't make this page.
        plan_events = [c for c in candidates if c["type"] == "plan_change"]
        if len(plan_events) >= limit:
            oldest_needed = sorted(
                (c["_sort"] for c in candidates), reverse=True
            )[:limit][-1]
            week_end_utc = _as_utc(
                datetime.combine(row.week_start, datetime.min.time())
            ) if row.week_start else None
            if week_end_utc is not None and week_end_utc < oldest_needed - timedelta(days=7):
                break

    candidates.sort(key=lambda c: c["_sort"], reverse=True)
    page = candidates[:limit]
    # Full page -> offer a cursor (older rows may exist; an empty follow-up
    # page ends paging cleanly). Short page -> both sources are exhausted.
    next_before = page[-1]["_sort"].isoformat() if len(page) == limit else None
    for c in page:
        c.pop("_sort", None)
    return {"events": page, "next_before": next_before}
