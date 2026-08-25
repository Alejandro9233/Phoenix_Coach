"""Tests for the injury recovery flow (chat's door swinging out).

Reporting an injury had a low-friction chat path; reporting that it healed did
not — the issue extractor explicitly ignores "my calf finally feels fine", and
resolving by hand never rebuilt the rest days `apply_issue` wrote. These tests
cover the mirror flow: detect recovery, match it to an active injury, resolve
it, and rebuild exactly the days that injury turned into rest.
"""
import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app, get_db
from backend.models.database import Base, Athlete, InjuryLog, WeeklyPlan
from backend.agents.response_agent import ResponseAgent
from backend.services.issue_triage import (
    _injury_rest_days,
    apply_recovery,
    build_recovery_proposal,
    extract_recovery,
    looks_like_recovery,
)
from backend.utils.timezone import get_local_today

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _training_day(name):
    # A short run with a PLAUSIBLE declared km (20 min / 3 km = 6:40/km)
    # keeps a full-week rebuild under the volume gate's ceiling on ANY
    # weekday — a Monday rebuild spans 7 days, and an implausible pace would
    # get the declared km discarded and re-estimated. This suite is about
    # recovery, not volume.
    return {
        "summary": f"{name} session",
        "workouts": [
            {"sport": "running", "title": "Easy Run", "steps": [],
             "total_time": "20:00", "distance_km": 3.0}
        ],
        "rationale": "train",
        "coach_note": "go",
    }


def _injury_rest_day(body_part, reported):
    return {
        "summary": f"Rest — {body_part}",
        "workouts": [{
            "sport": "rest",
            "title": "Rest",
            "steps": [],
            "total_time": "00:00",
            "hr_target": None,
            "enforced_reason": f"{body_part} (reported {reported})",
        }],
        "rationale": "",
        "coach_note": "",
    }


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    athlete = Athlete(name="Test Athlete")
    session.add(athlete)
    session.commit()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _seed_injury_and_week(session, body_part="Right calf"):
    """Active injury + a plan where every remaining day is rest because of it."""
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())

    athlete = session.query(Athlete).first()
    injury = InjuryLog(
        athlete_id=athlete.id,
        date_reported=today - timedelta(days=1),
        body_part=body_part,
        status="Active",
        severity=5,
        affected_sports="running",
        expected_recovery_date=today + timedelta(days=5),
    )
    session.add(injury)

    days = {}
    for i, name in enumerate(DAY_NAMES):
        if i < today.weekday():
            days[name] = _training_day(name)
        else:
            days[name] = _injury_rest_day(body_part, injury.date_reported)
    session.add(WeeklyPlan(week_start=week_start, athlete_id=athlete.id,
                           plan_json={"days": days}))
    session.commit()
    session.refresh(injury)
    return injury


# ─── detect ──────────────────────────────────────────────────────────────────

def test_looks_like_recovery_positives():
    for msg in [
        "my calf is fine now, fully recovered",
        "the calf doesn't hurt anymore",
        "ya no me duele la pantorrilla",
        "leg feels good, no pain at all",
        "estoy recuperado",
        "ya no duele la rodilla",
        "me siento mejor, sin dolor",
    ]:
        assert looks_like_recovery(msg), msg


def test_looks_like_recovery_negatives():
    for msg in [
        "my calf hurts",
        "still sore from yesterday",
        "what should we do this week?",
        "",
    ]:
        assert not looks_like_recovery(msg), msg


def test_extract_recovery_matches_active_injury(db_session, monkeypatch):
    injury = _seed_injury_and_week(db_session)

    def fake_chat(messages, json_mode=False, **kwargs):
        return '{"is_recovery": true, "injury_id": %d}' % injury.id

    monkeypatch.setattr("backend.core.llm_client.chat_completion", fake_chat)

    matched = extract_recovery("calf is fine now", [injury])
    assert matched is not None and matched.id == injury.id


def test_extract_recovery_rejects_non_recovery_and_bogus_ids(db_session, monkeypatch):
    injury = _seed_injury_and_week(db_session)

    monkeypatch.setattr(
        "backend.core.llm_client.chat_completion",
        lambda messages, json_mode=False, **kwargs: '{"is_recovery": false}',
    )
    assert extract_recovery("hi coach", [injury]) is None

    monkeypatch.setattr(
        "backend.core.llm_client.chat_completion",
        lambda messages, json_mode=False, **kwargs: '{"is_recovery": true, "injury_id": 999}',
    )
    assert extract_recovery("calf is fine", [injury]) is None


# ─── propose ─────────────────────────────────────────────────────────────────

def test_proposal_finds_only_this_injurys_rest_days(db_session):
    injury = _seed_injury_and_week(db_session)
    proposal = build_recovery_proposal(db_session, injury)

    today = get_local_today()
    expected = DAY_NAMES[today.weekday():]
    assert proposal["type"] == "recovery_proposal"
    assert proposal["injury"]["id"] == injury.id
    assert proposal["rebuild_days"] == expected


def test_injury_rest_days_ignores_other_reasons():
    today = get_local_today()
    days = {
        name: _injury_rest_day("Left knee", today) for name in DAY_NAMES
    }

    class FakeInjury:
        body_part = "Right calf"

    assert _injury_rest_days(days, FakeInjury(), today) == []


# ─── apply ───────────────────────────────────────────────────────────────────

def test_apply_recovery_resolves_and_rebuilds(db_session, monkeypatch):
    injury = _seed_injury_and_week(db_session)
    today = get_local_today()
    rebuild_days = DAY_NAMES[today.weekday():]

    def fake_generate(self, **kwargs):
        return {"days": {d: _training_day(d) for d in kwargs["days_to_plan"]}}

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", fake_generate)

    result = apply_recovery(db_session, injury.id)

    assert result["status"] == "resolved"
    assert result["rebuilt_days"] == sorted(rebuild_days)
    assert db_session.get(InjuryLog, injury.id).status == "Resolved"

    plan = db_session.query(WeeklyPlan).first()
    for d in rebuild_days:
        assert plan.plan_json["days"][d]["workouts"][0]["sport"] != "rest"


def test_apply_recovery_rebuild_failure_keeps_plan_and_resolve(db_session, monkeypatch):
    """The athlete IS recovered even when the LLM is down: resolve sticks,
    the plan stays untouched, and the error is reported — never a placeholder."""
    injury = _seed_injury_and_week(db_session)

    def boom(self, **kwargs):
        raise Exception("Error code: 404 - model_not_found (simulated)")

    monkeypatch.setattr(ResponseAgent, "generate_remaining_days", boom)

    result = apply_recovery(db_session, injury.id)

    assert result["status"] == "resolved"
    assert result["rebuilt_days"] == []
    assert "model_not_found" in result["rebuild_error"]
    assert db_session.get(InjuryLog, injury.id).status == "Resolved"

    plan = db_session.query(WeeklyPlan).first()
    today = get_local_today()
    for d in DAY_NAMES[today.weekday():]:
        assert plan.plan_json["days"][d]["workouts"][0]["sport"] == "rest"


def test_apply_recovery_is_idempotent(db_session, monkeypatch):
    injury = _seed_injury_and_week(db_session)
    monkeypatch.setattr(
        ResponseAgent, "generate_remaining_days",
        lambda self, **kw: {"days": {d: _training_day(d) for d in kw["days_to_plan"]}},
    )
    apply_recovery(db_session, injury.id)
    second = apply_recovery(db_session, injury.id)
    assert second["status"] == "already_resolved"


def test_apply_endpoint_unknown_injury_404(client):
    response = client.post("/coach/recovery/apply", json={"injury_id": 12345})
    assert response.status_code == 404


# ─── Recovering rows (auto-expired windows) ──────────────────────────────────
#
# An expired window parks an injury in "Recovering" — no longer blocking, but
# waiting for the athlete to say "it's fine". That state must be closable from
# chat too, or it's back to delete-being-the-only-exit.

def test_get_open_injuries_includes_recovering(db_session):
    from backend.services.issue_triage import get_open_injuries

    injury = _seed_injury_and_week(db_session)
    injury.status = "Recovering"
    db_session.commit()

    athlete = db_session.query(Athlete).first()
    open_ids = [i.id for i in get_open_injuries(db_session, athlete.id)]
    assert injury.id in open_ids


def test_apply_recovery_resolves_recovering_row(db_session, monkeypatch):
    injury = _seed_injury_and_week(db_session)
    injury.status = "Recovering"
    db_session.commit()

    monkeypatch.setattr(
        ResponseAgent, "generate_remaining_days",
        lambda self, **kw: {"days": {d: _training_day(d) for d in kw["days_to_plan"]}},
    )

    result = apply_recovery(db_session, injury.id)

    assert result["status"] == "resolved"
    assert db_session.get(InjuryLog, injury.id).status == "Resolved"
