"""The watch sync against a fake MCP: idempotent, read-before-write, fail-closed.

Every scenario the council listed as a way to create a duplicate or to strand a
day is a test here. The fake records writes; the real client is never touched.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, WatchWorkout
from backend.services import coros_mcp
from backend.services import coros_watch as cw
from backend.services import coros_watch_sync as ws

MON = date(2026, 3, 9)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close(); engine.dispose()


def _run(title, zone=2):
    return {"sport": "running", "title": title, "hr_target": str(zone),
            "steps": [{"type": "warmup", "duration": "10:00", "zone": 1},
                      {"type": "main", "duration": "30:00", "zone": zone},
                      {"type": "cooldown", "duration": "05:00", "zone": 1}]}


def _plan(**days):
    base = {d: {"workouts": []} for d in cw.WEEKDAYS}
    for k, v in days.items():
        base[k] = {"workouts": v}
    return {"days": base}


class FakeWatch(ws.WatchClient):
    """Scripted schedule text; records writes; hands out ids."""

    def __init__(self, items=None, text=None, fail_create=False):
        super().__init__(client=object())
        self.items = items or {}   # date -> list of (name, idInPlan, completed)
        self.text = text
        self.fail_create = fail_create
        self.writes = []
        self._next = 500

    def schedule_text(self, start, end):
        if self.text is not None:
            return self.text
        if not self.items:
            return f"No workouts scheduled for {start} to {end}."
        out = ["Training Schedule", "========================", ""]
        for d in sorted(self.items):
            out.append(d.isoformat())
            for name, iid, completed in self.items[d]:
                out += [name, "Standalone workout (scheduled from the workout library)", f"idInPlan: {iid}"]
                if completed:
                    out.append("Status: completed")
                out += ["Estimated Time: 45:00", "To view or modify this workout, use queryScheduledWorkoutDetails then updateScheduledWorkout with this date and idInPlan. To reschedule or remove it, use the COROS app."]
            out.append("")
        return "\n".join(out)

    def create(self, d, course):
        self.writes.append(("create", d, course["courseName"]))
        if self.fail_create:
            raise coros_mcp.CorosMcpError("createScheduledWorkout: HTTP 504")
        self._next += 1
        self.items.setdefault(d, []).append((course["courseName"], str(self._next), False))
        return str(self._next)

    def update(self, d, id_in_plan, course):
        self.writes.append(("update", d, id_in_plan, course["courseName"]))
        self.items[d] = [(course["courseName"] if iid == id_in_plan else n, iid, c) for n, iid, c in self.items[d]]
        return None   # COROS may or may not echo a new id


def test_off_switch_means_no_calls(db, monkeypatch):
    monkeypatch.delenv("COROS_WATCH_PUSH", raising=False)
    fake = FakeWatch()
    assert ws.sync_watch(db, _plan(Monday=[_run("Mon")]), MON, client=fake, today=MON)["status"] == "off"
    assert fake.writes == []


def test_first_push_creates_each_pushable_day_from_today_and_second_run_is_silent(db):
    plan = _plan(Monday=[_run("Mon easy")], Tuesday=[{"sport": "strength", "title": "Gym", "steps": []}],
                 Wednesday=[_run("Wed tempo", zone=4)], Sunday=[_run("Long run")])
    fake = FakeWatch()
    rep = ws.sync_watch(db, plan, MON, client=fake, today=MON + timedelta(days=1), force=True)   # Tuesday
    assert rep["status"] == "ok"
    assert [(w[0], w[1].isoformat(), w[2]) for w in fake.writes] == [
        ("create", "2026-03-11", "Phoenix 1 · Wed tempo"), ("create", "2026-03-15", "Phoenix 1 · Long run")]
    rows = {(r.date, r.slot): r for r in db.query(WatchWorkout).all()}
    assert rows[(date(2026, 3, 11), 1)].status == "pushed" and rows[(date(2026, 3, 11), 1)].id_in_plan == "501"
    assert rows[(date(2026, 3, 15), 1)].id_in_plan == "502"
    # Monday is in the past → untouched; strength never pushed
    assert (MON, 1) not in rows and (date(2026, 3, 10), 1) not in rows
    fake.writes.clear()
    rep2 = ws.sync_watch(db, plan, MON, client=fake, today=MON + timedelta(days=1), force=True)
    assert rep2["writes"] == [] and fake.writes == [] and rep2["status"] == "ok"
    assert {s["reason"] for s in rep2["skipped"]} == {"unchanged"}


def test_adaptation_updates_in_place_and_keeps_the_returned_id(db):
    plan = _plan(Wednesday=[_run("Wed tempo", zone=4)])
    fake = FakeWatch()
    ws.sync_watch(db, plan, MON, client=fake, today=MON, force=True)
    fake.writes.clear()
    fake.update = lambda d, iid, course: (fake.writes.append(("update", d, iid, course["courseName"])), "777")[1]
    fake.items[date(2026, 3, 11)] = [("Phoenix 1 · Wed tempo", "501", False)]
    adapted = _plan(Wednesday=[_run("Wed easy (adapted)", zone=2)])
    rep = ws.sync_watch(db, adapted, MON, days=["Wednesday"], client=fake, today=MON, force=True)
    assert fake.writes == [("update", date(2026, 3, 11), "501", "Phoenix 1 · Wed easy (adapted)")]
    row = db.query(WatchWorkout).filter_by(date=date(2026, 3, 11), slot=1).first()
    assert row.id_in_plan == "777" and row.status == "pushed" and rep["writes"][0]["ok"] is True


def test_stripped_day_becomes_cancelled_placeholder_once(db):
    fake = FakeWatch()
    ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    fake.writes.clear()
    stripped = _plan(Wednesday=[{"sport": "rest", "title": "Rest", "steps": [],
                                 "enforced_reason": "Active injury: calf rules out running"}])
    ws.sync_watch(db, stripped, MON, client=fake, today=MON, force=True)
    assert fake.writes == [("update", date(2026, 3, 11), "501",
                            "Phoenix 1 · CANCELLED — Active injury: calf rules out running")]
    row = db.query(WatchWorkout).filter_by(date=date(2026, 3, 11), slot=1).first()
    assert row.status == "cancelled"
    fake.writes.clear()
    rep = ws.sync_watch(db, stripped, MON, client=fake, today=MON, force=True)
    assert fake.writes == [] and rep["skipped"][0]["reason"] == "already cancelled on the watch"


def test_never_planned_and_nothing_on_watch_is_a_noop(db):
    fake = FakeWatch()
    rep = ws.sync_watch(db, _plan(Wednesday=[{"sport": "rest", "title": "Rest", "steps": [],
                                              "enforced_reason": "travel"}]), MON, client=fake, today=MON, force=True)
    assert fake.writes == [] and rep["writes"] == []   # no placeholder for something never pushed


def test_athlete_scheduled_day_is_left_alone(db):
    fake = FakeWatch(items={date(2026, 3, 11): [("Easy run", "82", False)]})
    rep = ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    assert fake.writes == []
    assert rep["skipped"][0]["reason"] == "athlete already scheduled: Easy run"
    assert db.query(WatchWorkout).first().status == "athlete_scheduled"


def test_unreadable_schedule_creates_nothing(db):
    fake = FakeWatch(text="Service exceptions")
    rep = ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    assert rep["status"] == "error" and "not recognised" in rep["error"] and fake.writes == []
    assert db.query(WatchWorkout).count() == 0


def test_schedule_read_exception_creates_nothing(db):
    fake = FakeWatch()
    fake.schedule_text = lambda a, b: (_ for _ in ()).throw(coros_mcp.CorosMcpError("HTTP 502"))
    rep = ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    assert rep["status"] == "error" and "schedule read failed" in rep["error"] and fake.writes == []


def test_completed_watch_item_is_locked_not_updated(db):
    fake = FakeWatch(items={date(2026, 3, 11): [("Phoenix 1 · Wed tempo", "501", True)]})
    rep = ws.sync_watch(db, _plan(Wednesday=[_run("Wed easy")]), MON, client=fake, today=MON, force=True)
    assert fake.writes == [] and rep["skipped"][0]["reason"].startswith("completed")
    assert db.query(WatchWorkout).first().status == "locked"


def test_failed_create_is_recorded_then_adopted_when_it_turns_out_to_have_landed(db):
    fake = FakeWatch(fail_create=True)
    rep = ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    row = db.query(WatchWorkout).filter_by(date=date(2026, 3, 11), slot=1).first()
    assert rep["status"] == "partial" and row.status == "failed" and "504" in row.last_error and row.attempts == 1
    # The create had actually landed (timeout after accept): the watch now shows it.
    fake.fail_create = False
    fake.items[date(2026, 3, 11)] = [("Phoenix 1 · Wed tempo", "600", False)]
    fake.writes.clear()
    ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    assert [w[0] for w in fake.writes] == ["update"]          # adopted, never a second create
    db.refresh(row)
    assert row.status == "pushed" and row.id_in_plan == "600" and row.attempts == 2


def test_athlete_deleted_our_workout_is_respected(db):
    fake = FakeWatch()
    ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    fake.items.clear()           # he removed it in the COROS app
    fake.writes.clear()
    rep = ws.sync_watch(db, _plan(Wednesday=[_run("Wed tempo")]), MON, client=fake, today=MON, force=True)
    assert fake.writes == [] and rep["skipped"][0]["reason"].startswith("removed by the athlete")
    assert db.query(WatchWorkout).first().status == "removed"


def test_two_runs_one_day_get_their_own_slots(db):
    fake = FakeWatch()
    plan = _plan(Wednesday=[{"sport": "strength", "title": "Core", "steps": []}, _run("AM easy"), _run("PM strides", zone=3)])
    ws.sync_watch(db, plan, MON, client=fake, today=MON, force=True)
    assert [w[2] for w in fake.writes] == ["Phoenix 1 · AM easy", "Phoenix 2 · PM strides"]
    assert sorted((r.slot, r.id_in_plan) for r in db.query(WatchWorkout).all()) == [(1, "501"), (2, "502")]


def test_mapper_refusal_skips_that_workout_only(db):
    fake = FakeWatch()
    plan = _plan(Wednesday=[_run("Bad", zone=None), _run("Good")])
    rep = ws.sync_watch(db, plan, MON, client=fake, today=MON, force=True)
    assert [w[2] for w in fake.writes] == ["Phoenix 2 · Good"] and rep["status"] == "ok"


def test_write_entry_allows_exactly_two_tools():
    client = coros_mcp.McpClient({"issuer": "https://mcpus.coros.com", "access_token": "x"})
    with pytest.raises(coros_mcp.CorosMcpError, match="only createScheduledWorkout, updateScheduledWorkout"):
        client.write("createTrainingPlan", {})
    with pytest.raises(coros_mcp.CorosMcpError, match="read-only"):
        client.call("createScheduledWorkout", {})
    assert client.calls == 0


def test_parse_id_in_plan_from_prose():
    assert ws.parse_id_in_plan("Scheduled workout created.\nDate: 2026-03-11\nidInPlan: 83\nName: Phoenix 1 · x") == "83"
    assert ws.parse_id_in_plan('{"idInPlan": "480874736383983618"}') == "480874736383983618"
    assert ws.parse_id_in_plan("saved") is None


def test_watch_status_reports_week_and_last_failure(db, monkeypatch):
    monkeypatch.delenv("COROS_WATCH_PUSH", raising=False)
    db.add(WatchWorkout(date=MON + timedelta(days=2), slot=1, course_name="Phoenix 1 · x", status="failed", last_error="HTTP 504"))
    db.add(WatchWorkout(date=MON + timedelta(days=4), slot=1, course_name="Phoenix 1 · y", status="pushed", id_in_plan="9"))
    db.commit()
    st = ws.watch_status(db, today=MON)
    assert st["enabled"] is False and st["week_start"] == "2026-03-09" and len(st["rows"]) == 2
    assert st["last_failure"] == {"date": "2026-03-11", "name": "Phoenix 1 · x", "error": "HTTP 504"}
