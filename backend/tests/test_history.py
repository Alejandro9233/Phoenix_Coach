"""The History system: refresh events, receipts with `after`, the merged feed.

Rules locked in here:
- Every plan write's receipt now carries `after` (captured at finalize, where
  the final plan is in hand — never reconstructed for new writes).
- A refresh ALWAYS records an event — a partial/failed scrape especially
  (failure must never mean silence); a lost log line never fails the sync.
- The feed merges refresh events + derived plan receipts newest-first by UTC
  instant; an auto-adapt's receipt folds INTO its refresh row.
- refresh_events prunes to the newest 200; regenerate carries receipts
  forward; the volume gate stays quiet about hours/quality in a tune-up race
  week (soft checks only).
- /weekly-plan/status carries run_km_done vs the C3 target, and the race
  block inside the final two weeks.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.response_agent import ResponseAgent
from backend.main import app, get_db, _run_smart_refresh
from backend.models.database import (
    Activity, Athlete, Base, RecoverySnapshot, RefreshEvent, WeeklyPlan,
)
from backend.services.history_feed import build_history_feed
from backend.services.refresh_events import (
    REFRESH_EVENTS_KEEP, build_refresh_event, record_refresh_event,
)
from backend.utils.timezone import get_local_now, get_local_today


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(
        name="Test", weight_kg=78.0,
        race_date=get_local_today() + timedelta(weeks=10),
        race_distance="Marathon", target_finish_time="3:10:00",
    ))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _fake_week():
    return {
        "week_summary": {"focus": "f", "rationale": "r",
                         "expected_total_hours": 8.0, "expected_run_km": 40.0},
        "days": {
            day: {"summary": "s", "workouts": [
                {"sport": "running", "title": "Easy Run", "total_time": "30 min",
                 "distance_km": 5.0, "steps": []}],
                "rationale": "r", "coach_note": "c"}
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
        },
    }


def _seed_plan(db, monkeypatch, client):
    monkeypatch.setattr(ResponseAgent, "generate_weekly_plan",
                        lambda self, *a, **kw: _fake_week())
    monkeypatch.setattr(ResponseAgent, "generate_weekly_review",
                        lambda self, *a, **kw: None, raising=False)
    resp = client.get("/weekly-plan")
    assert resp.status_code == 200, resp.text
    return db.query(WeeklyPlan).order_by(WeeklyPlan.id.desc()).first()


def _run(db, day, km, sport="running"):
    db.add(Activity(
        id=str(uuid.uuid4()), sport=sport,
        start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
        distance_m=km * 1000, duration_sec=km * 6 * 60, source="test",
    ))


# --- receipts carry `after` ---

def test_finalize_writes_after(db, monkeypatch, client):
    plan_row = _seed_plan(db, monkeypatch, client)
    receipt = plan_row.plan_json["_revisions"][-1]
    assert receipt["source"] == "generate"
    assert receipt["after"]["Monday"][0]["title"] == "Easy Run"


# --- status: run km + race block ---

def test_status_carries_run_km_numbers(db, monkeypatch, client):
    _seed_plan(db, monkeypatch, client)
    _run(db, get_local_today(), 12.0)
    db.commit()

    status = client.get("/weekly-plan/status").json()
    wp = status["week_progress"]
    assert wp["run_km_done"] == 12.0
    assert isinstance(wp["run_km_target"], (int, float))
    assert isinstance(wp["run_km_hard_cap"], (int, float))


def test_race_block_inside_final_two_weeks(db, monkeypatch, client):
    _seed_plan(db, monkeypatch, client)
    athlete = db.query(Athlete).first()

    status = client.get("/weekly-plan/status").json()
    assert status["race"] is None  # 10 weeks out

    athlete.race_date = get_local_today() + timedelta(days=3)
    db.commit()
    race = client.get("/weekly-plan/status").json()["race"]
    assert race["days_to_race"] == 3
    start_of_week = get_local_today() - timedelta(days=get_local_today().weekday())
    in_window = start_of_week <= athlete.race_date < start_of_week + timedelta(days=7)
    assert race["is_race_week"] is in_window
    assert race["pacing"]["splits"][-1]["cumulative"] == "3:10:00"


# --- refresh events ---

def _minimal_event(at=None):
    now = at or get_local_now()
    return {
        "schema_version": 1, "type": "refresh",
        "at": now.isoformat(timespec="seconds"),
        "local_day": now.date().isoformat(),
        "sync_status": "ok", "sync_message": "Biometrics synced.",
        "new_activity_count": 0, "new_activities": [],
        "recovery": {}, "recovery_stale": True, "stale_reason": "test",
        "recovery_delta": None, "triggers": [],
        "adaptation": {"needed": False, "adapted": False, "reasons": [],
                       "error": None, "week_start": None, "receipt_at": None},
        "week_after": None,
    }


def test_build_event_freezes_activity_compliance(db, monkeypatch, client):
    _seed_plan(db, monkeypatch, client)
    act_id = str(uuid.uuid4())
    db.add(Activity(id=act_id, sport="running",
                    start_time=datetime.combine(get_local_today(), datetime.min.time()) + timedelta(hours=7),
                    distance_m=7200, duration_sec=42 * 60, source="test"))
    yesterday = get_local_today() - timedelta(days=1)
    db.add(RecoverySnapshot(date=yesterday, hrv_ms=70.0, resting_hr=48, tib=5.0))
    db.commit()

    event = build_refresh_event(
        db, sync_status="ok", sync_message="ok",
        new_activity_ids=[act_id],
        recovery={"hrv_ms": 61.0, "resting_hr": 50, "tib": -2.0},
        recovery_stale=False, stale_reason=None,
        triggers=[], adaptation={"needed": False, "adapted": False,
                                 "reasons": [], "error": None,
                                 "week_start": None, "receipt_at": None},
    )
    assert event["new_activity_count"] == 1
    act = event["new_activities"][0]
    assert act["compliance"]["workout_title"] == "Easy Run"
    assert act["compliance"]["status"] in ("completed", "partial", "mismatch")
    assert event["recovery_delta"] == {"hrv_ms": -9.0, "resting_hr": 2, "tib": -7.0}
    assert event["week_after"]["run_km_done"] == 7.2


def test_delta_never_computed_against_stale(db):
    db.add(RecoverySnapshot(date=get_local_today() - timedelta(days=1),
                            hrv_ms=70.0, resting_hr=48))
    db.commit()
    event = build_refresh_event(
        db, sync_status="partial", sync_message="stale",
        new_activity_ids=[], recovery={"hrv_ms": 61.0},
        recovery_stale=True, stale_reason="old snapshot",
        triggers=[], adaptation={},
    )
    assert event["recovery_delta"] is None


def test_prune_keeps_newest(db):
    for i in range(REFRESH_EVENTS_KEEP + 5):
        record_refresh_event(db, _minimal_event(
            get_local_now() - timedelta(minutes=REFRESH_EVENTS_KEEP + 5 - i)))
    assert db.query(RefreshEvent).count() == REFRESH_EVENTS_KEEP


def test_partial_scrape_still_records_event(db, monkeypatch):
    import backend.main as main_mod

    class _BoomScraper:
        async def scrape_all(self, backfill_days=0):
            raise RuntimeError("coros down")

    monkeypatch.setattr(main_mod, "CorosScraper", _BoomScraper)
    result = asyncio.run(_run_smart_refresh(db))
    assert result["sync_status"] == "partial"
    assert "coros down" in result["sync_message"]
    assert result["event_recorded"] is True
    assert db.query(RefreshEvent).count() == 1
    assert db.query(RefreshEvent).first().sync_status == "partial"


def test_event_write_failure_never_fails_refresh(db, monkeypatch):
    import backend.main as main_mod

    class _BoomScraper:
        async def scrape_all(self, backfill_days=0):
            raise RuntimeError("coros down")

    monkeypatch.setattr(main_mod, "CorosScraper", _BoomScraper)
    # main imports record_refresh_event inside the function, so patching the
    # source module's attribute is enough.
    import backend.services.refresh_events as re_mod

    def _boom(db, e):
        raise RuntimeError("db full")

    monkeypatch.setattr(re_mod, "record_refresh_event", _boom)
    result = asyncio.run(_run_smart_refresh(db))
    assert result["event_recorded"] is False
    assert result["sync_status"] == "partial"  # refresh survived


# --- the merged feed ---

def test_feed_merges_and_folds(db, monkeypatch, client):
    plan_row = _seed_plan(db, monkeypatch, client)

    # A second receipt: an adapt_today write through the real pipeline.
    from backend.services.plan_meta import capture_before, run_plan_write_pipeline
    from backend.services.plan_normalizer import normalize_plan
    from sqlalchemy.orm.attributes import flag_modified

    plan_json = normalize_plan(plan_row.plan_json)
    before = capture_before(plan_json, days=["Sunday"])
    plan_json["days"]["Sunday"]["workouts"][0]["title"] = "Recovery Run"
    plan_json, _ = run_plan_write_pipeline(
        db, plan_json, source="adapt_today",
        availability={"run_days": "mon,tue,wed,thu,fri,sat,sun"},
        active_injuries=[], days=["Sunday"],
        reason="Today's workout adapted to recovery metrics.", before=before,
    )
    plan_row.plan_json = plan_json
    flag_modified(plan_row, "plan_json")
    db.commit()
    receipt_at = plan_json["_revisions"][-1]["at"]
    week_start = plan_row.week_start.isoformat()

    # A refresh event that CAUSED that adapt (join key = receipt_at).
    ev = _minimal_event()
    ev["adaptation"] = {"needed": True, "adapted": True, "reasons": ["HRV low"],
                        "error": None, "week_start": week_start,
                        "receipt_at": receipt_at}
    record_refresh_event(db, ev)

    feed = build_history_feed(db, limit=30)
    types = [e["type"] for e in feed["events"]]
    assert types.count("refresh") == 1
    # The adapt receipt folded into the refresh row — one generate receipt left
    plan_events = [e for e in feed["events"] if e["type"] == "plan_change"]
    assert len(plan_events) == 1
    assert plan_events[0]["source"] == "generate"
    refresh_ev = next(e for e in feed["events"] if e["type"] == "refresh")
    assert refresh_ev["adaptation"]["receipt"]["at"] == receipt_at
    # Newest first
    assert feed["events"][0]["type"] == "refresh"


def test_feed_reconstructs_legacy_after(db, monkeypatch, client):
    plan_row = _seed_plan(db, monkeypatch, client)
    from sqlalchemy.orm.attributes import flag_modified

    pj = dict(plan_row.plan_json)
    legacy = {
        "at": (get_local_now() - timedelta(hours=2)).isoformat(timespec="seconds"),
        "source": "replan_remaining", "days": ["Sunday"], "reason": "legacy",
        "before": {"Sunday": [{"sport": "running", "title": "Old Long Run",
                               "total_time": "90 min"}]},
        "stripped": [],
    }  # no "after" — pre-upgrade receipt
    pj["_revisions"] = [legacy] + list(pj["_revisions"])
    plan_row.plan_json = pj
    flag_modified(plan_row, "plan_json")
    db.commit()

    feed = build_history_feed(db, limit=30)
    legacy_ev = next(e for e in feed["events"] if e.get("reason") == "legacy")
    assert legacy_ev["after_source"] in ("reconstructed", "current")
    assert legacy_ev["after"]["Sunday"], "after must be filled from newer state"
    modern = next(e for e in feed["events"]
                  if e["type"] == "plan_change" and e["source"] == "generate")
    assert modern["after_source"] == "receipt"


def test_feed_pagination(db):
    base = get_local_now() - timedelta(hours=50)
    for i in range(45):
        record_refresh_event(db, _minimal_event(base + timedelta(hours=i)))
    page1 = build_history_feed(db, limit=30)
    assert len(page1["events"]) == 30
    assert page1["next_before"] is not None
    page2 = build_history_feed(db, limit=30, before=page1["next_before"])
    assert len(page2["events"]) == 15
    assert page2["next_before"] is None
    ids = {e["id"] for e in page1["events"]} | {e["id"] for e in page2["events"]}
    assert len(ids) == 45


def test_history_endpoint(db, monkeypatch, client):
    _seed_plan(db, monkeypatch, client)
    record_refresh_event(db, _minimal_event())
    body = client.get("/history").json()
    assert {e["type"] for e in body["events"]} == {"refresh", "plan_change"}


# --- regenerate carries receipts ---

def test_regenerate_preserves_receipts(db, monkeypatch, client):
    _seed_plan(db, monkeypatch, client)
    resp = client.post("/weekly-plan/regenerate")
    assert resp.status_code == 200, resp.text
    plan_row = db.query(WeeklyPlan).order_by(WeeklyPlan.id.desc()).first()
    revisions = plan_row.plan_json["_revisions"]
    assert len(revisions) == 2
    assert revisions[0]["source"] == "generate"
    assert revisions[-1]["source"] == "regenerate"


# --- gate hatches in a tune-up race week ---

def test_gate_quiet_in_tuneup_race_week():
    from backend.services import volume_gate

    ctx = {
        "phase": "build", "phase_name": "Build", "is_recovery_week": False,
        "recovery": {"status": "green"},
        "volume_targets": {"run_km_target": 20.0, "run_km_hard_cap": 25.0},
        "volume_references": {"phase_hours_range": "10-12",
                              "max_quality_sessions": 2,
                              "sport_sessions": {"running": {"sessions": 4}}},
        "tuneup": {"is_race_week": True},
    }
    light_plan = {
        "week_summary": {},
        "days": {"Tuesday": {"summary": "s", "workouts": [
            {"sport": "running", "title": "Easy Run", "total_time": "40 min",
             "distance_km": 7.0, "steps": []}]}},
    }
    availability = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}

    report = volume_gate.audit_plan(light_plan, ctx, availability=availability,
                                    active_injuries=[])
    kinds = [v["kind"] for v in report.soft]
    assert "hours_low" not in kinds
    assert "quality_missing" not in kinds

    ctx["tuneup"] = None  # control: without the race week both fire
    report = volume_gate.audit_plan(light_plan, ctx, availability=availability,
                                    active_injuries=[])
    kinds = [v["kind"] for v in report.soft]
    assert "hours_low" in kinds
    assert "quality_missing" in kinds
