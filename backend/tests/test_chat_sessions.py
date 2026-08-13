import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import json
import os
import tempfile
from backend.main import app, get_db
from backend.models.database import Base, Athlete, ChatSession, ChatMessage

from sqlalchemy.pool import StaticPool

@pytest.fixture
def test_db_session():
    # Use an in-memory database for tests, with StaticPool so threads share the same DB
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    # Create test athlete
    athlete = Athlete(name="Test Athlete", weight_kg=70.0)
    session.add(athlete)
    session.commit()
    
    yield session
    
    session.close()
    engine.dispose()

@pytest.fixture
def client(test_db_session):
    def override_get_db():
        yield test_db_session
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_chat_sessions_endpoints(client, test_db_session):
    # 1. Create a session
    response = client.post("/chat/sessions")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    session_id = data["id"]
    
    # 2. Get sessions
    response = client.get("/chat/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id
    
    # 3. Add a message via DB to test history
    msg1 = ChatMessage(session_id=session_id, role="user", content="Hello coach")
    msg2 = ChatMessage(session_id=session_id, role="assistant", content="Hello athlete")
    test_db_session.add(msg1)
    test_db_session.add(msg2)
    test_db_session.commit()
    
    # 4. Get session history
    response = client.get(f"/chat/sessions/{session_id}")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello coach"
    assert history[1]["role"] == "assistant"
    
    # 5. Delete session
    response = client.delete(f"/chat/sessions/{session_id}")
    assert response.status_code == 200
    
    response = client.get("/chat/sessions")
    assert len(response.json()) == 0

def test_chat_sync_creates_session(client, test_db_session, monkeypatch):
    # Mock LLM client so we don't actually call OpenAI/Ollama
    def mock_chat_completion(messages):
        return "This is a mock response."
    
    import backend.core.llm_client
    monkeypatch.setattr(backend.core.llm_client, "chat_completion", mock_chat_completion)
    
    # Mock check_llm_available to return connected so we don't skip anything
    def mock_check_llm_available():
        return {"status": "connected", "provider": "mock", "model": "mock"}
    monkeypatch.setattr(backend.core.llm_client, "check_llm_available", mock_check_llm_available)
    
    response = client.post("/chat-sync", json={"message": "What is my tempo pace?"})
    assert response.status_code == 200
    assert response.json()["response"] == "This is a mock response."
    
    # Check if session was created
    sessions = test_db_session.query(ChatSession).all()
    assert len(sessions) == 1
    session_id = sessions[0].id
    assert "What is my tempo pace" in sessions[0].title
    
    # Check if messages were saved
    messages = test_db_session.query(ChatMessage).filter_by(session_id=session_id).all()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "What is my tempo pace?"
    assert messages[1].role == "assistant"
    assert messages[1].content == "This is a mock response."
