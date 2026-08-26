"""
Refresh events — the durable record of what a smart-refresh found and did.

Alex's actual complaint: "most of the time idk whats happend after i refresh."
Until now nothing about a refresh survived the response payload — job state
was an in-memory dict, the payload was discarded, and a swallowed scrape or
adapt failure was invisible forever. This module freezes one event document
per refresh run:

- what synced (status, message, the new activities found — with the SAME
  compliance verdicts /weekly-plan/status computes, frozen at sync time),
- what the body said (recovery numbers, deltas vs yesterday's snapshot —
  never computed against a stale snapshot),
- every adaptation trigger with its value and threshold, fired or clear —
  "why did NOTHING happen" is half the question,
- what the plan did about it (adapted/not, the receipt timestamp joining this
  event to its _revisions entry, and the previously swallowed adapt error),
- where the week stands after (run km done vs target, sessions).

Storage: refresh_events rows, pruned to the newest REFRESH_EVENTS_KEEP on
insert. Recording failure must never fail a refresh (bookkeeping is not plan
data) — callers wrap record_refresh_event and set event_recorded=False.
The history feed (history_feed.py) merges these with plan receipts.
"""
from datetime import datetime, timedelta

from backend.models.database import Activity, RecoverySnapshot, RefreshEvent
from backend.utils.timezone import get_local_now, get_local_today

SCHEMA_VERSION = 1
REFRESH_EVENTS_KEEP = 200   # months of history for one athlete
MAX_ACTIVITIES_STORED = 20  # deep backfills report a count beyond this

# The five deterministic adaptation triggers, mirrored from _run_smart_refresh.
TRIGGER_NAMES = ("hrv_drop", "rhr_elevated", "fatigue_high",
                 "load_ratio_high", "tib_low")


def _activity_summary(db, activity) -> dict:
    """One new activity, with its compliance verdict frozen at sync time.

    Uses the same matcher/scorer the status endpoint uses so the debrief and
    the Today screen can never disagree about the same run."""
    from backend.services.compliance import (
        _compute_workout_compliance, _normalize_sport,
    )
    from backend.services.plan_normalizer import normalize_plan
    from backend.models.database import WeeklyPlan

    summary = {
        "activity_id": activity.id,
        "sport": activity.sport,
        "start_time": activity.start_time.isoformat() if activity.start_time else None,
        "distance_km": round((activity.distance_m or 0) / 1000, 2),
        "duration_min": round((activity.duration_sec or 0) / 60),
        "avg_hr": activity.avg_hr,
        "compliance": None,
    }

    if not activity.start_time:
        return summary
    act_day = activity.start_time.date()
    week_start = act_day - timedelta(days=act_day.weekday())
    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == week_start
    ).order_by(WeeklyPlan.id.desc()).first()
    if not plan_record:
        return summary

    day_plan = normalize_plan(plan_record.plan_json).get("days", {}).get(
        activity.start_time.strftime("%A")
    ) or {}
    act_sport = _normalize_sport(activity.sport)
    for workout in day_plan.get("workouts") or []:
        if _normalize_sport(workout.get("sport")) == act_sport:
            # Same dict shape the status endpoint feeds the scorer.
            verdict = _compute_workout_compliance(workout, {
                "duration_sec": activity.duration_sec,
                "avg_hr": activity.avg_hr,
                "distance_m": activity.distance_m,
            })
            summary["compliance"] = {
                "workout_title": workout.get("title"), **verdict,
            }
            break
    return summary


def _recovery_delta(db, recovery: dict, stale: bool) -> dict | None:
    """Today's numbers vs yesterday's snapshot. None when stale (a delta
    against stale numbers is a lie) or when either side is missing."""
    if stale or not recovery:
        return None
    yesterday = db.query(RecoverySnapshot).filter(
        RecoverySnapshot.date == get_local_today() - timedelta(days=1)
    ).first()
    if not yesterday:
        return None

    delta = {}
    for key, prev in (("hrv_ms", yesterday.hrv_ms),
                      ("resting_hr", yesterday.resting_hr),
                      ("tib", yesterday.tib)):
        cur = recovery.get(key)
        if cur is not None and prev is not None:
            delta[key] = round(cur - prev, 1)
    return delta or None


def _week_after(db) -> dict | None:
    """Where the week stands after this refresh — the status endpoint's own
    rollup, so the debrief's last line matches the Today screen."""
    from backend.services.compliance import get_weekly_plan_status

    status = get_weekly_plan_status(db)
    if not status:
        return None
    wp = status.get("week_progress") or {}
    return {
        "run_km_done": wp.get("run_km_done"),
        "run_km_target": wp.get("run_km_target"),
        "sessions_completed": wp.get("sessions_completed"),
        "sessions_planned": wp.get("sessions_planned"),
    }


def build_refresh_event(db, *, sync_status, sync_message, new_activity_ids,
                        recovery, recovery_stale, stale_reason, triggers,
                        adaptation) -> dict:
    """Assemble the frozen event document for one refresh run."""
    ids = list(new_activity_ids or [])
    stored = []
    if ids:
        rows = db.query(Activity).filter(
            Activity.id.in_(ids[:MAX_ACTIVITIES_STORED])
        ).all()
        rows.sort(key=lambda a: a.start_time or datetime.min, reverse=True)
        stored = [_activity_summary(db, a) for a in rows]

    return {
        "schema_version": SCHEMA_VERSION,
        "type": "refresh",
        "at": get_local_now().isoformat(timespec="seconds"),
        "local_day": get_local_today().isoformat(),
        "sync_status": sync_status,
        "sync_message": sync_message,
        "new_activity_count": len(ids),
        "new_activities": stored,
        "recovery": recovery,
        "recovery_stale": bool(recovery_stale),
        "stale_reason": stale_reason,
        "recovery_delta": _recovery_delta(db, recovery, recovery_stale),
        "triggers": triggers,
        "adaptation": adaptation,
        "week_after": _week_after(db),
    }


def record_refresh_event(db, event: dict) -> int:
    """Insert the event row and prune beyond REFRESH_EVENTS_KEEP. Returns the
    new row id. Raises on failure — the CALLER decides that a lost log line
    must not fail the sync (event_recorded=False in the payload)."""
    row = RefreshEvent(
        created_at=datetime.utcnow(),
        local_day=event["local_day"],
        at_local=event["at"],
        sync_status=event["sync_status"],
        payload_json=event,
    )
    db.add(row)
    db.flush()

    stale_ids = [
        r.id for r in db.query(RefreshEvent.id).order_by(
            RefreshEvent.created_at.desc(), RefreshEvent.id.desc()
        ).offset(REFRESH_EVENTS_KEEP).all()
    ]
    if stale_ids:
        db.query(RefreshEvent).filter(RefreshEvent.id.in_(stale_ids)).delete(
            synchronize_session=False
        )
    db.commit()
    return row.id
