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


def test_chat_stream_returns_session_id_and_continues(client, test_db_session, monkeypatch):
    """The one-message-sessions bug: /chat's SSE stream must announce the
    session it filed the exchange under as its FIRST frame, and a follow-up
    carrying that id must land in the same session — not mint a new one."""
    async def _fake_stream(messages):
        yield "ok"

    import backend.core.llm_client as llm
    monkeypatch.setattr(llm, "chat_completion_stream", _fake_stream)
    monkeypatch.setattr(
        "backend.agents.data_agent.DataAgent.summarize", lambda self: "metrics")

    class _KB:
        def query(self, *a, **k):
            return []

    import backend.main as main_mod
    monkeypatch.setattr(main_mod, "get_kb", lambda: _KB())

    # First message: no session_id known yet.
    resp = client.post("/chat", json={"message": "hello coach"})
    assert resp.status_code == 200
    frames = [json.loads(l[6:]) for l in resp.text.splitlines()
              if l.startswith("data: ") and l != "data: [DONE]"]
    assert "session_id" in frames[0], "session id must be the first frame"
    sid = frames[0]["session_id"]

    # Follow-up with the announced id: same session, no new one minted.
    resp = client.post("/chat", json={"message": "and my long run?",
                                      "session_id": sid})
    assert resp.status_code == 200
    frames = [json.loads(l[6:]) for l in resp.text.splitlines()
              if l.startswith("data: ") and l != "data: [DONE]"]
    assert frames[0]["session_id"] == sid

    assert test_db_session.query(ChatSession).count() == 1
    user_msgs = (test_db_session.query(ChatMessage)
                 .filter(ChatMessage.session_id == sid,
                         ChatMessage.role == "user").all())
    assert [m.content for m in user_msgs] == ["hello coach", "and my long run?"]
