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
    """Deterministic on any weekday: Sunday of the current week is always in
    the window; a date 8+ days out never is; a past race never shows."""
    _seed_plan(db, monkeypatch, client)
    athlete = db.query(Athlete).first()
    start_of_week = get_local_today() - timedelta(days=get_local_today().weekday())

    status = client.get("/weekly-plan/status").json()
    assert status["race"] is None  # 10 weeks out

    # Sunday of the current week: in the window, race week True.
    athlete.race_date = start_of_week + timedelta(days=6)
    db.commit()
    race = client.get("/weekly-plan/status").json()["race"]
    assert race["is_race_week"] is True
    assert race["days_to_race"] == (athlete.race_date - get_local_today()).days
    assert race["pacing"]["splits"][-1]["cumulative"] == "3:10:00"

    # Next week's Sunday: block present (weeks_to_race <= 1), not race week.
    athlete.race_date = start_of_week + timedelta(days=13)
    db.commit()
    race = client.get("/weekly-plan/status").json()["race"]
    assert race["is_race_week"] is False


def test_race_block_expires_after_race_day(db, monkeypatch, client):
    """weeks_to_race clamps past races to 0 — without the >= today guard the
    block (and 'Race in -1 days') would render forever."""
    _seed_plan(db, monkeypatch, client)
    athlete = db.query(Athlete).first()
    athlete.race_date = get_local_today() - timedelta(days=1)
    db.commit()
    assert client.get("/weekly-plan/status").json()["race"] is None


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
    # The SURVIVORS are the newest rows — pruning by created_at desc means
    # the highest ids remain (inserted newest-last above).
    surviving_ids = {r.id for r in db.query(RefreshEvent.id).all()}
    assert min(surviving_ids) == 6  # rows 1-5 (the oldest inserts) pruned


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
    # A NEWER receipt whose `before` covers Sunday: the legacy receipt's
    # after must be reconstructed from exactly this, not the live plan.
    newer = {
        "at": (get_local_now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        "source": "replan_remaining", "days": ["Sunday"], "reason": "newer",
        "before": {"Sunday": [{"sport": "running", "title": "Newer Before Run",
                               "total_time": "50 min"}]},
        "after": {"Sunday": [{"sport": "running", "title": "Final Run",
                              "total_time": "45 min"}]},
        "stripped": [],
    }
    pj["_revisions"] = [legacy] + list(pj["_revisions"]) + [newer]
    plan_row.plan_json = pj
    flag_modified(plan_row, "plan_json")
    db.commit()

    feed = build_history_feed(db, limit=30)
    legacy_ev = next(e for e in feed["events"] if e.get("reason") == "legacy")
    assert legacy_ev["after_source"] == "reconstructed"
    assert legacy_ev["after"]["Sunday"][0]["title"] == "Newer Before Run"
    modern = next(e for e in feed["events"]
                  if e["type"] == "plan_change" and e["source"] == "generate")
    assert modern["after_source"] == "receipt"


def test_feed_same_second_page_boundary(db):
    """Receipts and events share seconds precision; the page must extend
    through the boundary instant or the strict < cursor drops rows."""
    base = datetime.utcnow().replace(microsecond=0) - timedelta(hours=1)
    for i, created in enumerate([base, base, base - timedelta(minutes=5)]):
        ev = _minimal_event(get_local_now() - timedelta(minutes=i))
        row = RefreshEvent(created_at=created, local_day=ev["local_day"],
                           at_local=ev["at"], sync_status="ok", payload_json=ev)
        db.add(row)
    db.commit()

    page1 = build_history_feed(db, limit=1)
    assert len(page1["events"]) == 2  # both boundary-instant rows together
    page2 = build_history_feed(db, limit=1, before=page1["next_before"])
    assert len(page2["events"]) == 1
    ids = {e["id"] for e in page1["events"]} | {e["id"] for e in page2["events"]}
    assert len(ids) == 3  # nothing dropped, nothing duplicated


def test_cursor_survives_http_and_mangling(db, monkeypatch, client):
    """The cursor round-trips through the real endpoint, contains no '+'
    (a literal '+' form-decodes to a space server-side), and _as_utc repairs
    a mangled one anyway."""
    from backend.services.history_feed import _as_utc
    from datetime import timezone as tz

    base = get_local_now() - timedelta(hours=10)
    for i in range(5):
        record_refresh_event(db, _minimal_event(base + timedelta(hours=i)))

    page1 = client.get("/history", params={"limit": 3}).json()
    assert "+" not in page1["next_before"]
    page2 = client.get("/history",
                       params={"limit": 3, "before": page1["next_before"]}).json()
    assert len(page2["events"]) == 2
    assert {e["id"] for e in page2["events"]}.isdisjoint(
        {e["id"] for e in page1["events"]})

    # A '+00:00' cursor whose '+' was form-decoded to a space still parses.
    mangled = _as_utc("2026-08-25T20:00:00 00:00")
    assert mangled == datetime(2026, 8, 25, 20, 0, 0, tzinfo=tz.utc)
    assert _as_utc("2026-08-25T20:00:00Z") == mangled


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


# --- fresh-recovery triggers + the adapted truth ---

def _bad_recovery_today(db):
    athlete = db.query(Athlete).first()
    athlete.hrv_baseline = 70.0
    db.add(RecoverySnapshot(date=get_local_today(), hrv_ms=50.0,
                            resting_hr=60, fatigue_state=4,
                            load_ratio=1.5, tib=-25.0))
    db.commit()


def _stub_scraper(monkeypatch):
    import backend.main as main_mod

    class _EmptyScraper:
        async def scrape_all(self, backfill_days=0):
            return {}

    monkeypatch.setattr(main_mod, "CorosScraper", _EmptyScraper)
    return main_mod


def _fake_adapt(db, calls):
    """First call writes a real adapt_today receipt through the pipeline
    (like the real handler); later calls hit the same-day idempotency guard
    and write NOTHING — the exact behavior the adapted-truth check guards."""
    from sqlalchemy.orm.attributes import flag_modified
    from backend.services.plan_meta import capture_before, run_plan_write_pipeline
    from backend.services.plan_normalizer import normalize_plan

    def fake(body=None, db=db):
        calls["n"] += 1
        if calls["n"] > 1:
            return {"status": "already_adapted"}
        today_name = get_local_today().strftime("%A")
        week_start = get_local_today() - timedelta(days=get_local_today().weekday())
        plan_row = db.query(WeeklyPlan).filter(
            WeeklyPlan.week_start == week_start
        ).order_by(WeeklyPlan.id.desc()).first()
        pj = normalize_plan(plan_row.plan_json)
        before = capture_before(pj, days=[today_name])
        pj["days"][today_name]["workouts"][0]["title"] = "Recovery Run"
        pj, _ = run_plan_write_pipeline(
            db, pj, source="adapt_today",
            availability={"run_days": "mon,tue,wed,thu,fri,sat,sun"},
            active_injuries=[], days=[today_name],
            reason="Today's workout adapted to recovery metrics.", before=before,
        )
        plan_row.plan_json = pj
        flag_modified(plan_row, "plan_json")
        db.commit()
        return {"status": "adapted"}

    return fake


def test_fresh_triggers_recorded_and_adapt_joined(db, monkeypatch, client):
    _seed_plan(db, monkeypatch, client)
    _bad_recovery_today(db)
    main_mod = _stub_scraper(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(main_mod, "adapt_today_workout", _fake_adapt(db, calls))

    result = asyncio.run(_run_smart_refresh(db))
    event = result["event"]
    assert {t["name"] for t in event["triggers"]} == {
        "hrv_drop", "rhr_elevated", "fatigue_high", "load_ratio_high", "tib_low"}
    fired = {t["name"] for t in event["triggers"] if t["fired"]}
    # RHR can't fire: the 7-day avg IS today's single snapshot.
    assert fired == {"hrv_drop", "fatigue_high", "load_ratio_high", "tib_low"}
    hrv = next(t for t in event["triggers"] if t["name"] == "hrv_drop")
    assert hrv["value"] == -28.6 and hrv["threshold"] == -15

    assert event["adaptation"]["adapted"] is True
    week_start = get_local_today() - timedelta(days=get_local_today().weekday())
    plan_row = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == week_start).order_by(WeeklyPlan.id.desc()).first()
    assert event["adaptation"]["receipt_at"] == plan_row.plan_json["_revisions"][-1]["at"]

    # The feed folds the receipt into this refresh — one row, not two.
    feed = build_history_feed(db, limit=30)
    refresh_ev = next(e for e in feed["events"] if e["type"] == "refresh")
    assert refresh_ev["adaptation"]["receipt"]["source"] == "adapt_today"
    assert not any(e["type"] == "plan_change" and e["source"] == "adapt_today"
                   for e in feed["events"])


def test_second_refresh_never_claims_the_mornings_adapt(db, monkeypatch, client):
    """The idempotency guard writes nothing on refresh #2 — the event must
    say adapted=False and carry NO join key, or the feed folds the morning's
    receipt into an afternoon no-op."""
    _seed_plan(db, monkeypatch, client)
    _bad_recovery_today(db)
    main_mod = _stub_scraper(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(main_mod, "adapt_today_workout", _fake_adapt(db, calls))

    first = asyncio.run(_run_smart_refresh(db))
    second = asyncio.run(_run_smart_refresh(db))

    assert first["event"]["adaptation"]["adapted"] is True
    assert second["event"]["adaptation"]["needed"] is True
    assert second["event"]["adaptation"]["adapted"] is False
    assert second["event"]["adaptation"]["receipt_at"] is None


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


# --- the goal race week: the gate must not repair the marathon away ---

def _race_week_ctx():
    return {
        "phase": "taper", "phase_name": "Taper", "is_recovery_week": False,
        "recovery": {"status": "green"},
        "race_week": True, "race_day_name": "Sunday",
        # What the engine now computes for a marathon race week:
        # race km + shakeout allowance, not the taper ramp.
        "volume_targets": {"run_km_target": 50.2, "run_km_hard_cap": 56.2},
        "volume_references": {"phase_hours_range": "4-6",
                              "max_quality_sessions": 1,
                              "sport_sessions": {"running": {"sessions": 3}}},
        "workout_menu": {"running": ["Easy Run (short)", "Strides/Openers"]},
        "forbidden_workouts": [],
        "tuneup": None,
    }


def _race_week_plan():
    return {
        "week_summary": {},
        "days": {
            "Tuesday": {"summary": "s", "workouts": [
                {"sport": "running", "title": "Easy Run (short)",
                 "total_time": "25 min", "distance_km": 4.0, "steps": []}]},
            "Sunday": {"summary": "race", "workouts": [
                {"sport": "running", "title": "Marathon Race",
                 "total_time": "3:10:00", "distance_km": 42.2,
                 "steps": [{"type": "main", "duration": "190:00", "zone": 3,
                            "description": "Race at marathon pace"}]}]},
        },
    }


def test_gate_lets_the_goal_race_through(db):
    from backend.services import volume_gate

    report = volume_gate.audit_plan(
        _race_week_plan(), _race_week_ctx(),
        availability={"run_days": "mon,tue,wed,thu,fri,sat,sun"},
        active_injuries=[],
    )
    assert report.hard == [], [v["kind"] for v in report.hard]
    assert "hours_low" not in [v["kind"] for v in report.soft]


def test_same_plan_without_race_week_still_fails(db):
    """Control: an off-menu 42 km 'race' in a NORMAL taper week must still be
    hard-blocked — the exemption is the race week's race day only."""
    from backend.services import volume_gate

    ctx = _race_week_ctx()
    ctx["race_week"] = False
    ctx["race_day_name"] = None
    ctx["volume_targets"] = {"run_km_target": 18.0, "run_km_hard_cap": 31.5}
    report = volume_gate.audit_plan(
        _race_week_plan(), ctx,
        availability={"run_days": "mon,tue,wed,thu,fri,sat,sun"},
        active_injuries=[],
    )
    kinds = [v["kind"] for v in report.hard]
    assert "run_km_high" in kinds
    assert "forbidden_title" in kinds


def test_engine_race_week_budget_contains_the_race(db):
    """The marathon race week's target is race + shakeouts, not the taper
    ramp that would hard-cap the week below the race itself."""
    from backend.services.periodization_engine import (
        RACE_WEEK_EASY_CAP_KM, RACE_WEEK_EASY_KM,
    )

    athlete = db.query(Athlete).first()
    start_of_week = get_local_today() - timedelta(days=get_local_today().weekday())
    athlete.race_date = start_of_week + timedelta(days=6)
    db.commit()

    ctx = __import__("backend.services.periodization_engine",
                     fromlist=["PeriodizationEngine"]).PeriodizationEngine().compute_context(db)
    assert ctx["race_week"] is True
    vt = ctx["volume_targets"]
    assert vt["run_km_target"] == round(42.195 + RACE_WEEK_EASY_KM, 1)
    assert vt["run_km_hard_cap"] == round(42.195 + RACE_WEEK_EASY_CAP_KM, 1)
    assert vt["long_run_minutes"] == 0
    assert "goal race week" in vt["basis"]
