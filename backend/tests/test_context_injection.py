import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date, timedelta
from backend.models.database import Base, Athlete, WeeklyPlan
from backend.agents.data_agent import DataAgent
from backend.main import _build_chat_context
import json

@pytest.fixture
def temp_db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()

def test_data_agent_zones(temp_db_session):
    # Setup athlete with zones
    athlete = Athlete(
        name="Test Athlete",
        ftp_watts=200,
        hr_zones=[{"index": 0, "hr": 140}],
        pace_zones=[{"index": 0, "pace": 300}], # 5:00/km
        cycle_power_zones=[{"index": 0, "power": 150}]
    )
    temp_db_session.add(athlete)
    temp_db_session.commit()
    
    agent = DataAgent(temp_db_session)
    summary = agent.summarize()
    
    assert "Z1 <140bpm" in summary
    assert "Z1 5:00/km" in summary
    assert "Z1 <150W" in summary
    assert "Power Zones (FTP 200.0W)" in summary

def test_build_chat_context(temp_db_session):
    # Test that _build_chat_context injects the weekly plan correctly
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    
    # Create dummy weekly plan
    plan_json = {
        "days": {
            "Monday": {"summary": "Rest", "workouts": []},
            "Tuesday": {"summary": "Intervals", "workouts": [{"sport": "running", "title": "Track day", "steps": [], "total_time": "1h", "hr_target": "Z4"}]}
        }
    }
    
    plan = WeeklyPlan(
        week_start=start_of_week,
        athlete_id=1,
        plan_json=plan_json
    )
    temp_db_session.add(plan)
    temp_db_session.commit()
    
    summary = "SUMMARY_TEXT"
    rag_context = "RAG_TEXT"
    
    system_prompt = _build_chat_context(temp_db_session, summary, rag_context)
    
    assert "SUMMARY_TEXT" in system_prompt
    assert "RAG_TEXT" in system_prompt
    assert "FULL WEEK SCHEDULE" in system_prompt
    assert "- Monday: Rest" in system_prompt
    assert "- Tuesday: Intervals" in system_prompt
    assert "TODAY'S WORKOUT" in system_prompt
