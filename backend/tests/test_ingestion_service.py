import pytest
import json
import os
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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
