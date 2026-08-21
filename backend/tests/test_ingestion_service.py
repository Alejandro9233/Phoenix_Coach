import pytest
import json
import os
import tempfile
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.models.database import Base, Athlete
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
