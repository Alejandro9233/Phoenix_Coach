"""
Sync the plan onto the COROS watch calendar — idempotent, read-before-write.

Council (2026-10-07): push the week once after the plan commits, edit the
day on adaptation, never duplicate, never prescribe on a stripped day.

How a run decides, per (date, slot), from today forward:
1. One `queryTrainingSchedule` read for the span. If the text is not
   recognisably a schedule, STOP: nothing is created. An empty parse must
   never read as "nothing scheduled".
2. Match Phoenix items on the watch by name (`Phoenix <slot> · …`); the
   `watch_workouts` row is the memory of what we did and with which id.
3. Planned course present:
   - on the watch → `update` when the hash changed (or the id drifted), else skip;
   - not on the watch, never pushed → `create`, unless the athlete already
     scheduled something himself that day (then skip, say so);
   - not on the watch, but we had pushed it → he deleted it in the app → skip.
4. Planned course absent but Phoenix has something on the watch for that
   slot → overwrite with the CANCELLED placeholder (COROS cannot delete).
5. A completed watch item is locked by COROS → skip.
Each write records a row BEFORE the call (status creating) and after it
(pushed/cancelled/failed with the returned id), so a timeout that landed is
recognised on the next run by the Phoenix name on the watch, not re-created.

Off unless COROS_WATCH_PUSH=1 and the MCP token exists. Runs after
db.commit(), outside _PLAN_GENERATION_LOCK (a rolled-back plan must never
leave courses on the watch).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from backend.models.database import WatchWorkout
from backend.services import coros_mcp
from backend.services import coros_watch as cw
from backend.utils.timezone import get_local_now, get_local_today

KEEP_DAYS = 60
_ID_RE = re.compile(r"idInPlan\W{0,4}(\d{1,20})", re.I)


def push_enabled() -> bool:
    flag = os.getenv("COROS_WATCH_PUSH", "0").strip().lower()
    return flag in ("1", "true", "on", "yes") and coros_mcp.enabled()


def parse_id_in_plan(text: str) -> str | None:
    """The create/update answer is prose; the id is the number after idInPlan."""
    m = _ID_RE.search(text or "")
    return m.group(1) if m else None


def schedule_is_readable(text: str) -> bool:
    """A populated calendar starts 'Training Schedule'; an empty range answers
    'No training schedule found.' (seen live 2026-10-08). Anything else —
    'Service exceptions', an auth page, a reworded template — is unknown, and
    unknown must never read as 'nothing scheduled'."""
    t = (text or "").strip().lower()
    if t.startswith("training schedule"):
        return True
    return t.startswith("no ") and ("schedule" in t or "workout" in t)


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

class WatchClient:
    """Thin wrapper so the sync can be tested with a fake."""

    def __init__(self, client: coros_mcp.McpClient | None = None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = coros_mcp.McpClient(coros_mcp.ensure_token())
        return self._client

    def schedule_text(self, start: date, end: date) -> str:
        return self.client.call_text("queryTrainingSchedule", {
            "startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d")})

    def create(self, d: date, course: dict) -> str | None:
        res = self.client.write("createScheduledWorkout", {"date": d.strftime("%Y%m%d"), "course": course})
        return parse_id_in_plan(coros_mcp.tool_text(res))

    def update(self, d: date, id_in_plan: str, course: dict) -> str | None:
        res = self.client.write("updateScheduledWorkout", {
            "date": d.strftime("%Y%m%d"), "idInPlan": str(id_in_plan), "course": course})
        return parse_id_in_plan(coros_mcp.tool_text(res))


# --------------------------------------------------------------------------
# Decisions (pure)
# --------------------------------------------------------------------------

@dataclass
class Action:
    date: date
    slot: int
    kind: str            # create | update | skip
    reason: str
    course: dict | None = None
    id_in_plan: str | None = None
    cancel: bool = False


def decide_actions(week: dict[date, cw.DayCourses], schedule: dict[date, list[dict]],
                   rows: dict[tuple[date, int], WatchWorkout], dates: list[date]) -> list[Action]:
    actions = []
    for d in dates:
        dc = week.get(d) or cw.DayCourses(date=d)
        items = schedule.get(d, [])
        on_watch = cw.phoenix_items(items)
        athlete_items = [i for i in items if not i.get("phoenix_slot")]
        planned = dict(dc.courses)
        slots = set(planned) | set(on_watch) | {s for (rd, s) in rows if rd == d}
        for slot in sorted(slots):
            row = rows.get((d, slot))
            watch = on_watch.get(slot)
            course = planned.get(slot)
            cancel = False
            if course is None:
                if watch is None:
                    actions.append(Action(d, slot, "skip", "not planned, nothing on the watch"))
                    continue
                if cw.NAME_RE.match(watch["name"]).group(2).startswith(cw.CANCELLED_MARK):
                    actions.append(Action(d, slot, "skip", "already cancelled on the watch", id_in_plan=watch["idInPlan"]))
                    continue
                course = cw.cancelled_course(slot, dc.enforced_reason or "session removed by the plan")
                cancel = True
            h = cw.course_hash(course)
            if watch is not None:
                if watch.get("completed"):
                    actions.append(Action(d, slot, "skip", "completed on the watch (locked)", id_in_plan=watch["idInPlan"]))
                elif row is not None and row.course_hash == h and row.id_in_plan == watch["idInPlan"] \
                        and row.status in ("pushed", "cancelled"):
                    actions.append(Action(d, slot, "skip", "unchanged", id_in_plan=watch["idInPlan"]))
                else:
                    actions.append(Action(d, slot, "update", "cancel" if cancel else "course changed",
                                          course=course, id_in_plan=watch["idInPlan"], cancel=cancel))
                continue
            # nothing of ours on the watch for this slot
            if row is not None and row.id_in_plan and row.status in ("pushed", "cancelled"):
                actions.append(Action(d, slot, "skip", "removed by the athlete in the COROS app"))
            elif athlete_items and row is None:
                names = ", ".join(i["name"] for i in athlete_items)
                actions.append(Action(d, slot, "skip", f"athlete already scheduled: {names}"))
            else:
                actions.append(Action(d, slot, "create", "new", course=course, cancel=cancel))
    return actions


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------

def _row(db, d, slot) -> WatchWorkout:
    row = db.query(WatchWorkout).filter_by(date=d, slot=slot).first()
    if row is None:
        row = WatchWorkout(date=d, slot=slot, status="creating", attempts=0)
        db.add(row)
    return row


def sync_watch(db, plan_json: dict, week_start: date, days: list[str] | None = None,
               client: WatchClient | None = None, today: date | None = None,
               force: bool = False) -> dict:
    """Push/update/cancel this week's courses from today forward. Returns a
    report; never raises (callers run it after the plan commit and must not
    fail a request over the watch). `days` limits to weekday names."""
    report = {"status": "off", "writes": [], "skipped": [], "error": None}
    if not force and not push_enabled():
        return report
    today = today or get_local_today()
    week = cw.plan_day_courses(plan_json, week_start)
    dates = [d for d in sorted(week) if d >= today and (days is None or d.strftime("%A") in days)]
    report["status"] = "nothing"
    if not dates:
        return report
    client = client or WatchClient()
    try:
        text = client.schedule_text(dates[0], dates[-1])
    except Exception as e:
        report.update(status="error", error=f"schedule read failed: {type(e).__name__}: {e}"[:200])
        return report
    if not schedule_is_readable(text):
        report.update(status="error", error="schedule text not recognised — refusing to create anything")
        return report
    schedule = cw.parse_training_schedule(text)
    rows = {(r.date, r.slot): r for r in db.query(WatchWorkout).filter(WatchWorkout.date.in_(dates)).all()}

    for a in decide_actions(week, schedule, rows, dates):
        if a.kind == "skip":
            if a.reason.startswith("athlete already scheduled"):
                row = _row(db, a.date, a.slot); row.status = "athlete_scheduled"; row.last_error = a.reason
            elif a.reason.startswith("removed by the athlete"):
                rows[(a.date, a.slot)].status = "removed"
            elif a.reason.startswith("completed"):
                row = _row(db, a.date, a.slot); row.status = "locked"; row.id_in_plan = a.id_in_plan
            report["skipped"].append({"date": a.date.isoformat(), "slot": a.slot, "reason": a.reason})
            continue
        row = _row(db, a.date, a.slot)
        row.attempts = (row.attempts or 0) + 1
        row.course_name = a.course["courseName"]
        row.status = "creating" if a.kind == "create" else "updating"
        db.commit()  # write-ahead: a crash mid-call leaves a trace
        entry = {"date": a.date.isoformat(), "slot": a.slot, "action": a.kind, "name": a.course["courseName"]}
        try:
            if a.kind == "create":
                new_id = client.create(a.date, a.course)
            else:
                new_id = client.update(a.date, a.id_in_plan, a.course)
            row.id_in_plan = new_id or a.id_in_plan
            row.course_hash = cw.course_hash(a.course)
            row.status = "cancelled" if a.cancel else "pushed"
            row.last_error = None
            row.pushed_at = get_local_now().replace(tzinfo=None)
            entry.update(ok=True, id_in_plan=row.id_in_plan)
        except Exception as e:
            row.status = "failed"
            row.last_error = f"{type(e).__name__}: {e}"[:300]
            entry.update(ok=False, error=row.last_error)
        db.commit()
        report["writes"].append(entry)

    cutoff = today - timedelta(days=KEEP_DAYS)
    db.query(WatchWorkout).filter(WatchWorkout.date < cutoff).delete(synchronize_session=False)
    db.commit()
    report["status"] = "ok" if all(w.get("ok") for w in report["writes"]) else "partial"
    return report


def watch_status(db, today: date | None = None) -> dict:
    """For the app: this week's rows and the newest failure."""
    today = today or get_local_today()
    week_start = today - timedelta(days=today.weekday())
    rows = db.query(WatchWorkout).filter(WatchWorkout.date >= week_start,
                                         WatchWorkout.date <= week_start + timedelta(days=6)) \
        .order_by(WatchWorkout.date, WatchWorkout.slot).all()
    failures = [r for r in rows if r.status == "failed"]
    return {
        "enabled": push_enabled(),
        "week_start": week_start.isoformat(),
        "rows": [{"date": r.date.isoformat(), "slot": r.slot, "name": r.course_name, "status": r.status,
                  "id_in_plan": r.id_in_plan, "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None,
                  "error": r.last_error} for r in rows],
        "last_failure": ({"date": failures[-1].date.isoformat(), "name": failures[-1].course_name,
                          "error": failures[-1].last_error} if failures else None),
    }
