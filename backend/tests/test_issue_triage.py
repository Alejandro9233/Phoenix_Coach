"""
Tests for chat-driven injury triage.

Covers the pure-Python halves — the keyword gate, the proposal builder, and the
apply path's rest branch. The LLM extraction call is stubbed; what matters is
that a bad or absent extraction degrades to normal chat rather than mangling a
training week.
"""
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base, InjuryLog, WeeklyPlan
from backend.services.issue_triage import (
    apply_issue,
    build_proposal,
    extract_issue,
    looks_like_issue,
)

# A fixed Wednesday. Real "today" would make these tests pass or fail depending
# on the weekday they run — on a Sunday there is exactly one remaining day, so
# any assertion about multiple affected days silently breaks once a week.
FROZEN_TODAY = date(2026, 8, 19)
FROZEN_WEEK_START = date(2026, 8, 17)  # the Monday of that week


@pytest.fixture(autouse=True)
def frozen_today():
    """Pin the service's idea of today. It's the only clock the triage path reads."""
    with patch("backend.services.issue_triage.get_local_today", return_value=FROZEN_TODAY):
        yield


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def _seed(db, **plan_days):
    """Seed an athlete plus this week's plan. Day names are full English names."""
    athlete = Athlete(
        name="Test",
        swim_days="wed,sat,sun",
        bike_days="mon,tue,wed,thu,fri,sat,sun",
        run_days="mon,tue,wed,thu,fri,sat,sun",
        strength_days="mon,wed,fri",
    )
    db.add(athlete)
    db.commit()
    db.refresh(athlete)

    db.add(WeeklyPlan(
        week_start=FROZEN_WEEK_START,
        athlete_id=athlete.id,
        plan_json={"days": plan_days},
    ))
    db.commit()
    return athlete


def _run(title="6km tempo"):
    return {"sport": "running", "title": title, "steps": [], "total_time": "45:00"}


def _all_week(workout_factory):
    from backend.services.plan_normalizer import VALID_DAYS
    return {d: {"summary": d, "workouts": [workout_factory()]} for d in VALID_DAYS}


# ─── keyword gate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message", [
    "my right calf is so sore I can't run",
    "knee pain after the tempo",
    "I think I strained my hamstring",
    "shin splints again",
    "tengo dolor en la pantorrilla",
    "mi rodilla tiene una molestia",
    "me duele la pantorrilla, no puedo correr",
    "me lastimé el tobillo en la carrera",
])
def test_gate_catches_issue_reports(message):
    assert looks_like_issue(message)


@pytest.mark.parametrize("message", [
    "how did my week go?",
    "what should tomorrow's session be?",
    "the tempo run felt great",
    "",
])
def test_gate_ignores_ordinary_chat(message):
    """The gate runs on every message — a false positive costs a real LLM call."""
    assert not looks_like_issue(message)


# ─── extraction ───────────────────────────────────────────────────────────────

def test_extraction_returns_none_when_not_an_issue():
    with patch("backend.core.llm_client.chat_completion", return_value='{"is_issue": false}'):
        assert extract_issue("legs felt heavy today") is None


def test_extraction_returns_none_on_llm_failure():
    """Triage breaking must degrade to normal chat, never raise into the stream."""
    with patch("backend.core.llm_client.chat_completion", side_effect=RuntimeError("groq down")):
        assert extract_issue("my calf is sore") is None


def test_extraction_returns_none_on_malformed_json():
    with patch("backend.core.llm_client.chat_completion", return_value="not json at all"):
        assert extract_issue("my calf is sore") is None


def test_extraction_clamps_out_of_range_values():
    payload = (
        '{"is_issue": true, "body_part": "Right calf", "severity": 99, '
        '"affected_sports": ["run"], "duration_days": 400, "notes": "n"}'
    )
    with patch("backend.core.llm_client.chat_completion", return_value=payload):
        issue = extract_issue("calf is destroyed")
    assert issue["severity"] == 10
    assert issue["duration_days"] == 14


def test_extraction_rejects_an_issue_that_blocks_nothing():
    payload = '{"is_issue": true, "body_part": "Calf", "severity": 4, "affected_sports": []}'
    with patch("backend.core.llm_client.chat_completion", return_value=payload):
        assert extract_issue("calf feels a bit off") is None


# ─── proposal ─────────────────────────────────────────────────────────────────

def _issue(**over):
    base = {
        "body_part": "Right calf",
        "severity": 6,
        "affected_sports": ["run"],
        "duration_days": 7,
        "notes": "sore after tempo",
        "coach_note": "",
    }
    base.update(over)
    return base


def test_proposal_lists_affected_days_with_both_options(db):
    _seed(db, **_all_week(_run))

    proposal = build_proposal(db, _issue())

    assert proposal["issue"]["affected_sports"] == ["running"]
    assert proposal["affected_days"], "running every day should collide"
    for day in proposal["affected_days"]:
        assert {o["id"] for o in day["options"]} >= {"rest"}


def test_proposal_never_touches_days_already_trained(db):
    """You cannot un-run yesterday's run."""
    _seed(db, **_all_week(_run))

    proposal = build_proposal(db, _issue())

    from backend.services.plan_normalizer import VALID_DAYS
    for day in proposal["affected_days"]:
        assert VALID_DAYS.index(day["day"]) >= FROZEN_TODAY.weekday()
    assert "Tuesday" not in [d["day"] for d in proposal["affected_days"]]


def test_proposal_respects_the_duration_window(db):
    _seed(db, **_all_week(_run))

    one_day = build_proposal(db, _issue(duration_days=1))
    week = build_proposal(db, _issue(duration_days=7))

    assert len(one_day["affected_days"]) == 1
    assert len(week["affected_days"]) >= len(one_day["affected_days"])


def test_proposal_is_none_when_nothing_collides(db):
    """No card for an issue that changes nothing."""
    from backend.services.plan_normalizer import VALID_DAYS
    swim_week = {d: {"summary": d, "workouts": [
        {"sport": "swimming", "title": "Technique", "steps": [], "total_time": "40:00"}
    ]} for d in VALID_DAYS}
    _seed(db, **swim_week)

    assert build_proposal(db, _issue(affected_sports=["run"])) is None


def test_proposal_is_none_without_a_plan(db):
    db.add(Athlete(name="Test"))
    db.commit()

    assert build_proposal(db, _issue()) is None


def test_swap_option_never_offers_an_unavailable_sport(db):
    """Offering a Sunday swim the athlete can't do is worse than offering rest."""
    _seed(db, **_all_week(_run))

    proposal = build_proposal(db, _issue(duration_days=14))

    from backend.services.constraint_enforcer import DAY_ABBR, parse_day_list
    swim_ok = parse_day_list("wed,sat,sun")
    bike_ok = parse_day_list("mon,tue,wed,thu,fri,sat,sun")
    for day in proposal["affected_days"]:
        swap = next((o for o in day["options"] if o["id"] == "swap"), None)
        if swap and swap["sport"] == "swimming":
            assert DAY_ABBR[day["day"]] in swim_ok
        if swap and swap["sport"] == "cycling":
            assert DAY_ABBR[day["day"]] in bike_ok


# ─── apply ────────────────────────────────────────────────────────────────────

def test_apply_logs_the_injury_as_active(db):
    _seed(db, **_all_week(_run))
    proposal = build_proposal(db, _issue())
    day = proposal["affected_days"][0]["day"]

    result = apply_issue(db, _issue(), {day: "rest"})

    injury = db.query(InjuryLog).filter(InjuryLog.id == result["injury_id"]).one()
    assert injury.status == "Active"
    assert injury.body_part == "Right calf"
    assert injury.affected_sports == "running"


def test_apply_rest_choice_clears_the_day_with_a_reason(db):
    _seed(db, **_all_week(_run))
    proposal = build_proposal(db, _issue())
    day = proposal["affected_days"][0]["day"]

    result = apply_issue(db, _issue(), {day: "rest"})

    workouts = result["plan"]["days"][day]["workouts"]
    assert [w["sport"] for w in workouts] == ["rest"]
    assert "Right calf" in workouts[0]["enforced_reason"]


def test_apply_strips_blocked_sessions_on_days_with_no_choice(db):
    """
    An omitted day must fail safe.

    The enforcer runs over every affected day regardless of what the athlete
    picked, so a day the card missed can never keep a session the injury forbids.
    """
    _seed(db, **_all_week(_run))
    proposal = build_proposal(db, _issue(duration_days=7))
    days = [d["day"] for d in proposal["affected_days"]]
    assert len(days) >= 2, "need at least two affected days for this test"

    apply_issue(db, _issue(), {days[0]: "rest"})

    # Re-read the persisted plan and enforce over the whole week.
    from backend.services.constraint_enforcer import enforce_constraints, get_active_injuries
    from backend.services.plan_normalizer import normalize_plan
    athlete = db.query(Athlete).first()
    record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == FROZEN_WEEK_START
    ).order_by(WeeklyPlan.id.desc()).first()

    _, violations = enforce_constraints(
        normalize_plan(record.plan_json),
        active_injuries=get_active_injuries(db, athlete.id),
        days=days,
    )
    assert violations == [], f"blocked sessions survived: {violations}"


def test_apply_falls_back_to_rest_when_regeneration_fails(db):
    """A dead LLM must not leave a forbidden session standing."""
    _seed(db, **_all_week(_run))
    proposal = build_proposal(db, _issue())
    day = proposal["affected_days"][0]["day"]

    with patch(
        "backend.agents.response_agent.ResponseAgent.generate_remaining_days",
        side_effect=RuntimeError("llm down"),
    ):
        result = apply_issue(db, _issue(), {day: "swap"})

    assert [w["sport"] for w in result["plan"]["days"][day]["workouts"]] == ["rest"]


def test_apply_raises_without_a_plan(db):
    db.add(Athlete(name="Test"))
    db.commit()

    with pytest.raises(ValueError):
        apply_issue(db, _issue(), {})


# ─── endpoints ────────────────────────────────────────────────────────────────
#
# In-memory SQLite behind a get_db override, so these are safe to run on a
# machine holding production credentials (see CLAUDE.md).

@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from backend.main import app, get_db

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_preview_endpoint_requires_a_message(client):
    assert client.post("/coach/issue/preview", json={}).status_code == 400


def test_preview_endpoint_returns_a_proposal(client, db):
    _seed(db, **_all_week(_run))

    response = client.post("/coach/issue/preview", json={"message": "calf is sore", "issue": _issue()})

    assert response.status_code == 200
    body = response.json()
    assert body["detected"] is True
    assert body["proposal"]["issue"]["body_part"] == "Right calf"
    assert body["proposal"]["affected_days"]


def test_preview_endpoint_writes_nothing(client, db):
    """The whole point of the preview/apply split."""
    _seed(db, **_all_week(_run))

    client.post("/coach/issue/preview", json={"message": "calf is sore", "issue": _issue()})

    assert db.query(InjuryLog).count() == 0


def test_preview_endpoint_reports_when_nothing_is_affected(client, db):
    _seed(db, **_all_week(_run))

    response = client.post(
        "/coach/issue/preview",
        json={"message": "shoulder hurts", "issue": _issue(affected_sports=["swim"])},
    )

    assert response.json()["detected"] is False


def test_apply_endpoint_requires_an_issue(client):
    assert client.post("/coach/issue/apply", json={"choices": {}}).status_code == 400


def test_apply_endpoint_rejects_a_non_map_choices(client):
    response = client.post("/coach/issue/apply", json={"issue": _issue(), "choices": ["Monday"]})
    assert response.status_code == 400


def test_apply_endpoint_404s_without_a_plan(client, db):
    db.add(Athlete(name="Test"))
    db.commit()

    response = client.post("/coach/issue/apply", json={"issue": _issue(), "choices": {}})
    assert response.status_code == 404


def test_apply_endpoint_updates_the_plan(client, db):
    _seed(db, **_all_week(_run))

    response = client.post(
        "/coach/issue/apply",
        json={"issue": _issue(), "choices": {"Wednesday": "rest"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["rest_days"] == ["Wednesday"]
    assert [w["sport"] for w in body["plan"]["days"]["Wednesday"]["workouts"]] == ["rest"]
    assert db.query(InjuryLog).count() == 1


# ─── data_agent injury visibility ─────────────────────────────────────────────
#
# summarize() reads injuries through constraint_enforcer.get_active_injuries,
# so an injury past its expected_recovery_date must never reach the prompt as
# ACTIVE — and the read has the enforcer's side effect of flipping the row to
# Recovering. These use the real clock: dates are built relative to
# get_local_today, so they hold on any run date (the frozen_today fixture only
# pins issue_triage's clock, not data_agent's or the enforcer's).

def _seed_injury(db, expected_recovery_date):
    from datetime import timedelta

    from backend.utils.timezone import get_local_today

    athlete = Athlete(name="Test")
    db.add(athlete)
    db.commit()
    db.refresh(athlete)

    injury = InjuryLog(
        athlete_id=athlete.id,
        date_reported=get_local_today() - timedelta(days=5),
        body_part="Right calf",
        status="Active",
        severity=6,
        affected_sports="running",
        expected_recovery_date=expected_recovery_date,
    )
    db.add(injury)
    db.commit()
    return injury


def test_summarize_moves_an_expired_injury_to_recovering(db):
    from datetime import timedelta

    from backend.agents.data_agent import DataAgent
    from backend.utils.timezone import get_local_today

    injury = _seed_injury(db, get_local_today() - timedelta(days=1))

    summary = DataAgent(db).summarize()

    assert "ACTIVE INJURIES" not in summary
    assert "RECOVERING" in summary
    db.refresh(injury)
    assert injury.status == "Recovering"


def test_summarize_keeps_an_injury_active_through_its_recovery_date(db):
    from backend.agents.data_agent import DataAgent
    from backend.utils.timezone import get_local_today

    injury = _seed_injury(db, get_local_today())

    summary = DataAgent(db).summarize()

    assert "ACTIVE INJURIES" in summary
    db.refresh(injury)
    assert injury.status == "Active"


def test_summarize_keeps_a_dateless_injury_active(db):
    """Legacy rows (expected_recovery_date NULL) last until resolved by hand."""
    from backend.agents.data_agent import DataAgent

    injury = _seed_injury(db, None)

    summary = DataAgent(db).summarize()

    assert "ACTIVE INJURIES" in summary
    db.refresh(injury)
    assert injury.status == "Active"
