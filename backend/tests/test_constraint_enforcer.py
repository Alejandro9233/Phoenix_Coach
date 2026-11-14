"""
Tests for the hard availability/injury gate.

Regression origin: an athlete removed Sunday from strength_days, hit
/weekly-plan/replan-remaining, and the Sunday strength session survived. The
constraint existed only as prompt text; the LLM ignored it and nothing checked.
"""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base, InjuryLog
from backend.services.constraint_enforcer import (
    enforce_constraints,
    get_active_injuries,
    parse_day_list,
)


def _plan(**days):
    return {"days": {name: day for name, day in days.items()}}


def _day(*workouts):
    return {"summary": "test day", "workouts": list(workouts)}


def _workout(sport, title=None):
    return {"sport": sport, "title": title or f"{sport} session", "steps": [], "total_time": "45:00"}


def _injury(body_part="Right calf", affected_sports="run", severity=6):
    return SimpleNamespace(body_part=body_part, affected_sports=affected_sports, severity=severity)


# ─── parse_day_list ───────────────────────────────────────────────────────────

def test_parse_day_list_none_means_unconstrained():
    assert parse_day_list(None) is None


def test_parse_day_list_empty_string_means_never():
    """An athlete who clears every strength day wants zero strength, not any day."""
    assert parse_day_list("") == set()


def test_parse_day_list_normalizes_case_and_length():
    assert parse_day_list("Mon, WEDNESDAY ,fri") == {"mon", "wed", "fri"}


# ─── availability ─────────────────────────────────────────────────────────────

def test_strips_strength_on_unavailable_day():
    plan = _plan(Sunday=_day(_workout("strength", "Full body lift")))

    plan, violations = enforce_constraints(plan, availability={"strength_days": "mon,wed,fri"})

    assert len(violations) == 1
    assert violations[0]["day"] == "Sunday"
    assert violations[0]["sport"] == "strength"
    assert "not available on Sunday" in violations[0]["reason"]


def test_emptied_day_becomes_an_explained_rest_day():
    """A vanished session must be visible, not a silent blank."""
    plan = _plan(Sunday=_day(_workout("strength")))

    plan, _ = enforce_constraints(plan, availability={"strength_days": "mon,wed,fri"})

    sunday = plan["days"]["Sunday"]
    assert [w["sport"] for w in sunday["workouts"]] == ["rest"]
    assert "not available on Sunday" in sunday["workouts"][0]["enforced_reason"]
    assert sunday["enforcement_notes"]


def test_keeps_other_workouts_on_a_partially_violating_day():
    plan = _plan(Sunday=_day(_workout("running", "Long run"), _workout("strength")))

    plan, violations = enforce_constraints(
        plan,
        availability={"strength_days": "mon,wed,fri", "run_days": "mon,tue,wed,thu,fri,sat,sun"},
    )

    titles = [w["title"] for w in plan["days"]["Sunday"]["workouts"]]
    assert titles == ["Long run"]
    assert len(violations) == 1


def test_allows_sport_on_an_available_day():
    plan = _plan(Wednesday=_day(_workout("strength")))

    plan, violations = enforce_constraints(plan, availability={"strength_days": "mon,wed,fri"})

    assert violations == []
    assert len(plan["days"]["Wednesday"]["workouts"]) == 1


def test_missing_availability_key_means_unconstrained():
    plan = _plan(Sunday=_day(_workout("swimming")))

    plan, violations = enforce_constraints(plan, availability={})

    assert violations == []


def test_rest_is_never_a_violation():
    plan = _plan(Sunday=_day(_workout("rest", "Rest")))

    plan, violations = enforce_constraints(plan, availability={"strength_days": ""})

    assert violations == []


# ─── injuries ─────────────────────────────────────────────────────────────────

def test_active_injury_blocks_the_affected_sport():
    plan = _plan(Thursday=_day(_workout("running", "6km tempo")))

    plan, violations = enforce_constraints(plan, active_injuries=[_injury()])

    assert len(violations) == 1
    assert "Right calf" in violations[0]["reason"]
    assert "severity 6/10" in violations[0]["reason"]


def test_injury_does_not_block_unaffected_sports():
    plan = _plan(Thursday=_day(_workout("swimming", "Technique swim")))

    plan, violations = enforce_constraints(plan, active_injuries=[_injury(affected_sports="run")])

    assert violations == []


def test_injury_with_no_affected_sports_blocks_nothing():
    """Inferring which sports a body part rules out is coaching, not arithmetic."""
    plan = _plan(Thursday=_day(_workout("running")))

    plan, violations = enforce_constraints(plan, active_injuries=[_injury(affected_sports=None)])

    assert violations == []


def test_injury_sport_aliases_are_matched():
    plan = _plan(Thursday=_day(_workout("cycling", "Endurance ride")))

    plan, violations = enforce_constraints(plan, active_injuries=[_injury(affected_sports="bike")])

    assert len(violations) == 1


# ─── day scoping ──────────────────────────────────────────────────────────────

def test_scoping_leaves_locked_days_untouched():
    """Mid-week replans must never rewrite days the athlete already trained."""
    plan = _plan(
        Monday=_day(_workout("strength", "Already done")),
        Sunday=_day(_workout("strength", "Should be stripped")),
    )

    plan, violations = enforce_constraints(
        plan,
        availability={"strength_days": "wed"},
        days=["Sunday"],
    )

    assert [v["day"] for v in violations] == ["Sunday"]
    assert plan["days"]["Monday"]["workouts"][0]["title"] == "Already done"


def test_clean_plan_reports_no_violations():
    plan = _plan(Wednesday=_day(_workout("strength"), _workout("swimming")))

    plan, violations = enforce_constraints(
        plan,
        availability={"strength_days": "mon,wed,fri", "swim_days": "wed,sat,sun"},
    )

    assert violations == []
    assert len(plan["days"]["Wednesday"]["workouts"]) == 2


# ─── injury expiry ────────────────────────────────────────────────────────────
#
# Every enforcement path reads injuries through get_active_injuries, so expiry
# is handled there once rather than at each call site.

TODAY = date(2026, 8, 19)


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


@pytest.fixture(autouse=True)
def frozen_today():
    with patch("backend.utils.timezone.get_local_today", return_value=TODAY):
        yield


def _log_injury(db, athlete_id, recovery_offset_days=None, status="Active"):
    injury = InjuryLog(
        athlete_id=athlete_id,
        date_reported=TODAY,
        body_part="Right calf",
        status=status,
        severity=6,
        affected_sports="running",
        expected_recovery_date=(
            TODAY + timedelta(days=recovery_offset_days)
            if recovery_offset_days is not None else None
        ),
    )
    db.add(injury)
    db.commit()
    db.refresh(injury)
    return injury


@pytest.fixture
def athlete(db):
    a = Athlete(name="Test")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_injury_inside_its_window_stays_active(db, athlete):
    _log_injury(db, athlete.id, recovery_offset_days=2)

    assert len(get_active_injuries(db, athlete.id)) == 1


def test_injury_on_its_recovery_date_is_still_active(db, athlete):
    """The window is inclusive — the last day still counts."""
    _log_injury(db, athlete.id, recovery_offset_days=0)

    assert len(get_active_injuries(db, athlete.id)) == 1


def test_expired_injury_stops_blocking_and_is_marked_recovering(db, athlete):
    """A 3-day calf niggle must not ban running for the rest of the season."""
    injury = _log_injury(db, athlete.id, recovery_offset_days=-1)

    assert get_active_injuries(db, athlete.id) == []

    db.refresh(injury)
    assert injury.status == "Recovering"


def test_injury_without_a_recovery_date_never_expires(db, athlete):
    """Hand-entered and pre-existing rows keep the old until-resolved behaviour."""
    _log_injury(db, athlete.id, recovery_offset_days=None)

    assert len(get_active_injuries(db, athlete.id)) == 1


def test_resolved_injuries_are_ignored(db, athlete):
    _log_injury(db, athlete.id, recovery_offset_days=2, status="Resolved")

    assert get_active_injuries(db, athlete.id) == []


def test_sport_days_defaults_only_the_unset_fields():
    from types import SimpleNamespace

    from backend.services.constraint_enforcer import DEFAULT_SPORT_DAYS, sport_days

    athlete = SimpleNamespace(swim_days=None, bike_days="", run_days="tue,thu",
                              strength_days=None)
    assert sport_days(athlete) == {
        "swim_days": DEFAULT_SPORT_DAYS["swim_days"],
        "bike_days": "",          # set to "no days" on purpose — not defaulted
        "run_days": "tue,thu",
        "strength_days": DEFAULT_SPORT_DAYS["strength_days"],
    }
    assert sport_days(None) == DEFAULT_SPORT_DAYS


def test_no_file_spells_out_the_sport_days_itself():
    """The four *_days fields are read through sport_days(); 13 hand-written
    dicts used to disagree on what an unset field means."""
    import re
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    for path in backend.rglob("*.py"):
        if "tests" in path.parts:
            continue
        assert not re.search(r'"swim_days": athlete\.', path.read_text()), (
            f"{path.name} builds the sport days by hand — use sport_days(athlete)")
