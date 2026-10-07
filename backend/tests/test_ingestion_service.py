import pytest
import json
import os
import tempfile
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.models.database import Base, Athlete, Activity, RecoverySnapshot
from backend.services.ingestion_service import IngestionService

@pytest.fixture
def temp_db_url():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    
    # Initialize DB schema and seed athlete
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    athlete = Athlete(name="Test Athlete", weight_kg=70.0)
    session.add(athlete)
    session.commit()
    session.close()
    engine.dispose()
    
    yield db_url
    
    # Cleanup
    os.unlink(db_path)

@pytest.fixture
def mock_coros_json():
    return {
        "activities": [],
        "evolab": {
            "dashboard_query": {
                "weight": 76.0,
                "headPic": "https://s3.coros.com/avatar/test",
                "zoneData": {
                    "cyclePowerZone": [
                        {"index": 0, "power": 101, "ratio": 56.0}
                    ],
                    "ftp": 180,
                    "lthrZone": [
                        {"hr": 142, "index": 0, "ratio": 80.0}
                    ],
                    "ltspZone": [
                        {"index": 0, "pace": 374, "ratio": 71.1}
                    ]
                }
            }
        }
    }

def test_ingest_coros_zones_and_profile(temp_db_url, mock_coros_json):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(mock_coros_json, f)
        temp_json_path = f.name
        
    try:
        service = IngestionService(db_url=temp_db_url)
        service.ingest_coros_data(temp_json_path)
        
        # Verify the database was updated
        engine = create_engine(temp_db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        athlete = session.query(Athlete).first()
        
        assert athlete.weight_kg == 76.0
        assert athlete.head_pic_url == "https://s3.coros.com/avatar/test"
        assert athlete.ftp_watts == 180.0
        assert athlete.cycle_power_zones == [{"index": 0, "power": 101, "ratio": 56.0}]
        assert athlete.hr_zones == [{"hr": 142, "index": 0, "ratio": 80.0}]
        assert athlete.pace_zones == [{"index": 0, "pace": 374, "ratio": 71.1}]
        
        session.close()
        engine.dispose()
    finally:
        os.unlink(temp_json_path)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _read_athlete(db_url):
    """Load the athlete from a file-backed test DB in a throwaway session."""
    engine = create_engine(db_url)
    session = sessionmaker(bind=engine)()
    try:
        return session.query(Athlete).first()
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(temp_db_url):
    """TestClient wired to the same file-backed DB the IngestionService uses.

    In-memory SQLite behind a get_db override, so safe on a machine holding
    production credentials (see CLAUDE.md) — here file-backed, but still a
    throwaway sqlite the override pins every request to.
    """
    from fastapi.testclient import TestClient
    from backend.main import app, get_db

    engine = create_engine(temp_db_url)
    Session = sessionmaker(bind=engine)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


# ─── weight: the watch owns it, always ───────────────────────────────────────
# Alex maintains weight in COROS and wants the scrape to propagate it — a
# Profile edit is a temporary value until the next scrape, by choice
# (2026-08-21). Do not add an app-wins guard here.

def test_ingest_always_updates_weight(temp_db_url, mock_coros_json):
    IngestionService(db_url=temp_db_url).ingest_coros_data(mock_coros_json)

    assert _read_athlete(temp_db_url).weight_kg == 76.0


def test_ingest_returns_new_ids_once(temp_db_url, mock_coros_json):
    """The refresh-event contract: first ingest returns the new Activity ids,
    a re-ingest of the same payload returns [] (dedupe means nothing new)."""
    payload = dict(mock_coros_json)
    payload["activities"] = [{
        "labelId": 424242, "timestamp": 1787000000, "duration": 2520,
        "distance": 7000, "sportType": 100, "avgSpeed": 360,
    }]
    first = IngestionService(db_url=temp_db_url).ingest_coros_data(payload)
    assert first == ["424242"]

    second = IngestionService(db_url=temp_db_url).ingest_coros_data(payload)
    assert second == []


def test_scrape_overwrites_app_entered_weight_by_design(client, temp_db_url, mock_coros_json):
    response = client.put("/athlete/profile", json={"weight_kg": 71})
    assert response.status_code == 200
    assert _read_athlete(temp_db_url).weight_kg == 71

    IngestionService(db_url=temp_db_url).ingest_coros_data(mock_coros_json)

    assert _read_athlete(temp_db_url).weight_kg == 76.0

    IngestionService(db_url=temp_db_url).ingest_coros_data(mock_coros_json)

    assert _read_athlete(temp_db_url).weight_kg == 76.0


# ─── lthr: COROS threshold HR stops masquerading as hr_max (C4) ──────────────

def test_ingest_lthr_lands_in_lthr_not_hr_max(temp_db_url):
    payload = {
        "activities": [],
        "evolab": {
            "analyse_query": {
                "dayList": [{"happenDay": 20260818, "lthr": 165}],
            },
        },
    }

    IngestionService(db_url=temp_db_url).ingest_coros_data(payload)

    athlete = _read_athlete(temp_db_url)
    assert athlete.lthr == 165
    assert athlete.hr_max is None


def test_lthr_migration_backfills_once():
    """The backfill moves hr_max→lthr exactly once; a second boot must not
    re-null a future genuine hr_max (the trap: UPDATEs outside the guard)."""
    from backend.main import _migrate_athletes

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        # Pre-migration shape: hr_max holds COROS LTHR, no lthr column yet
        conn.execute(text(
            "CREATE TABLE athletes (id INTEGER PRIMARY KEY, name VARCHAR, hr_max INTEGER)"
        ))
        conn.execute(text("INSERT INTO athletes (name, hr_max) VALUES ('Alex', 178)"))

    _migrate_athletes(engine)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT lthr, hr_max FROM athletes")).one()
    assert row.lthr == 178
    assert row.hr_max is None

    # Simulate a genuine max HR arriving later, then a second boot
    with engine.begin() as conn:
        conn.execute(text("UPDATE athletes SET hr_max = 190"))
    _migrate_athletes(engine)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT lthr, hr_max FROM athletes")).one()
    assert row.lthr == 178
    assert row.hr_max == 190
    engine.dispose()


def test_stale_hr_max_rewrite_heals_on_next_boot():
    """During the lthr deploy an old instance re-wrote hr_max with the LTHR
    value after the backfill nulled it. The one-shot guard won't re-run, so
    the every-boot cleanup must clear the equal case — and only that case."""
    from backend.main import _migrate_athletes

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE athletes (id INTEGER PRIMARY KEY, name VARCHAR, hr_max INTEGER)"
        ))
        conn.execute(text("INSERT INTO athletes (name, hr_max) VALUES ('Alex', 177)"))

    _migrate_athletes(engine)

    # Deploy overlap: an old instance writes the stale LTHR value back.
    with engine.begin() as conn:
        conn.execute(text("UPDATE athletes SET hr_max = 177"))

    _migrate_athletes(engine)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT lthr, hr_max FROM athletes")).one()
    assert row.lthr == 177
    assert row.hr_max is None
    engine.dispose()


def test_recovery_pct_annotates_todays_snapshot(temp_db_url):
    """dashboard_query.summaryInfo.recoveryPct lands on today's snapshot as
    recovery_score — the column had no writer since the Garmin era. The row
    itself comes from analyse_query.dayList (the real metrics ingest)."""
    from backend.models.database import RecoverySnapshot
    from backend.utils.timezone import get_local_today

    today_int = int(get_local_today().strftime("%Y%m%d"))
    payload = {
        "activities": [],
        "evolab": {
            "analyse_query": {"dayList": [{"happenDay": today_int}]},
            "dashboard_query": {"summaryInfo": {"recoveryPct": 78}},
        },
    }
    IngestionService(db_url=temp_db_url).ingest_coros_data(payload)

    engine = create_engine(temp_db_url)
    session = sessionmaker(bind=engine)()
    try:
        snap = session.query(RecoverySnapshot).filter_by(
            date=get_local_today()).first()
        assert snap is not None
        assert snap.recovery_score == 78.0
    finally:
        session.close()
        engine.dispose()


def test_recovery_pct_never_mints_a_snapshot(temp_db_url):
    """A partial scrape (dashboard captured, analyse_query missing) must NOT
    create a today-dated row: a bare row with every gate metric NULL would
    make the adapt-today staleness guard read stale data as fresh."""
    from backend.models.database import RecoverySnapshot

    payload = {
        "activities": [],
        "evolab": {"dashboard_query": {"summaryInfo": {"recoveryPct": 78}}},
    }
    IngestionService(db_url=temp_db_url).ingest_coros_data(payload)

    engine = create_engine(temp_db_url)
    session = sessionmaker(bind=engine)()
    try:
        assert session.query(RecoverySnapshot).count() == 0
    finally:
        session.close()
        engine.dispose()


def test_missing_recovery_pct_is_harmless(temp_db_url):
    from backend.models.database import RecoverySnapshot

    payload = {
        "activities": [],
        "evolab": {"dashboard_query": {"summaryInfo": {}}},
    }
    IngestionService(db_url=temp_db_url).ingest_coros_data(payload)

    engine = create_engine(temp_db_url)
    session = sessionmaker(bind=engine)()
    try:
        for snap in session.query(RecoverySnapshot).all():
            assert snap.recovery_score is None
    finally:
        session.close()
        engine.dispose()


# --- MCP-shaped payloads (docs/COROS_MCP.md) ---------------------------------
# Same ingest, same writer. The MCP path omits keys it couldn't parse and adds
# the sleep/stress fields that were NULL for the scraper's whole life.

def _snapshot(db_url, day):
    from datetime import date as _date
    engine = create_engine(db_url)
    with sessionmaker(bind=engine)() as s:
        return s.query(RecoverySnapshot).filter_by(date=_date(*day)).first()


def _mcp_day(**extra):
    base = {"happenDay": 20260308, "avgSleepHrv": 52, "testRhr": 54, "ati": 46, "cti": 60,
            "tib": 14.0, "tiredRateNew": -14.0, "tiredRateStateNew": 2,
            "trainingLoadRatio": 0.76, "trainingLoadRatioState": 2}
    base.update(extra)
    return {"activities": [], "evolab": {
        "analyse_query": {"dayList": [base]},
        "dashboard_query": {"summaryInfo": {"sleepHrvData": {"sleepHrvList": [
            {"happenDay": 20260308, "avgSleepHrv": 52, "sleepHrvBase": 80}]}}}}}


def test_mcp_day_writes_sleep_and_stress_and_never_invents_zeros(temp_db_url):
    IngestionService(db_url=temp_db_url).ingest_coros_data(
        _mcp_day(sleepScore=82, sleepDurationMin=500, stressAvg=30))
    snap = _snapshot(temp_db_url, (2026, 3, 8))
    assert snap.hrv_ms == 52 and snap.hrv_baseline == 80 and snap.resting_hr == 54
    assert snap.ati == 46 and snap.cti == 60 and snap.tib == 14 and snap.fatigue_state == 2
    assert snap.sleep_quality_score == 82 and snap.sleep_duration_hr == 8.33 and snap.stress_level == 30
    # keys the payload didn't carry stay NULL — not 0.0 as the old code wrote
    assert snap.hrv_sd is None and snap.vo2_max is None and snap.lthr is None
    assert snap.training_load is None and snap.recommend_tl_min is None


def test_mcp_day_without_a_key_preserves_the_column(temp_db_url):
    svc = IngestionService(db_url=temp_db_url)
    svc.ingest_coros_data(_mcp_day(sleepScore=82, sleepDurationMin=500, stressAvg=30))
    # A later pull parsed only the resting HR line for that day.
    svc.ingest_coros_data({"activities": [], "evolab": {"analyse_query": {"dayList": [
        {"happenDay": 20260308, "testRhr": 55}]}}})
    snap = _snapshot(temp_db_url, (2026, 3, 8))
    assert snap.resting_hr == 55
    assert snap.ati == 46 and snap.sleep_quality_score == 82 and snap.hrv_ms == 52


def test_mcp_activity_uses_local_start_time_name_calories_source(temp_db_url):
    from datetime import datetime as _dt
    act = {"labelId": "900000000000000001", "sportType": 101, "timestamp": 1772996400,
           "startTimeLocal": "2026-03-08T20:30:00", "duration": 2100, "distance": 4890.0,
           "avgHeartRate": 151, "avgSpeed": 429.45, "avgPower": 0, "totalElevation": 0,
           "trainingLoad": 65, "pitch": 151, "sets": None, "subMode": None,
           "name": "Easy run", "calories": 396, "source": "coros_mcp"}
    ids = IngestionService(db_url=temp_db_url).ingest_coros_data({"activities": [act], "evolab": {}})
    assert ids == ["900000000000000001"]
    engine = create_engine(temp_db_url)
    with sessionmaker(bind=engine)() as s:
        row = s.query(Activity).filter_by(id="900000000000000001").first()
    assert row.start_time == _dt(2026, 3, 8, 20, 30)   # local wall clock, not UTC
    assert row.source == "coros_mcp" and row.activity_name == "Easy run" and row.calories == 396
    assert row.sport == "running" and row.cadence == 151 and row.training_load == 65
    assert round(row.avg_speed_ms, 3) == round(1000 / 429.45, 3)
    # the same labelId again is a no-op (dedupe by id), even with a different timestamp
    again = IngestionService(db_url=temp_db_url).ingest_coros_data({"activities": [dict(act, timestamp=1)], "evolab": {}})
    assert again == []
