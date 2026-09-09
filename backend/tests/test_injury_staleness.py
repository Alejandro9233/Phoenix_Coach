"""An injury row describes the day it was written, not today.

2026-09-09: the athlete reported his ankle was better and asked to cycle only.
The coach replied "keep the bike off ... your injury is still logged as severity
8/10", quoting a row written three days earlier when he could not walk, and
presented the enforcer's mechanical Rest day as a deliberate clinical decision.
`data_agent` had handed it severity and blocked sports with no date at all, so
nothing in the prompt said the record was stale.

These tests pin the date, the age, and the explicit stale marker.
"""
import pytest
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.data_agent import DataAgent, INJURY_STALE_DAYS
from backend.models.database import Base, Athlete, InjuryLog
from backend.utils.timezone import get_local_today


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    session.add(Athlete(name="Test Athlete"))
    session.commit()
    yield session
    session.close()


def _log(session, *, days_ago, recovery_in=None, status="Active"):
    today = get_local_today()
    session.add(InjuryLog(
        athlete_id=1,
        date_reported=today - timedelta(days=days_ago),
        body_part="Ankle",
        status=status,
        severity=8,
        notes="i got my ankle really hurt during the long run, i can't even walk.",
        affected_sports="cycling,running",
        expected_recovery_date=(
            today + timedelta(days=recovery_in) if recovery_in is not None else None
        ),
    ))
    session.commit()


def test_active_injury_carries_report_date_and_age(db_session):
    _log(db_session, days_ago=3, recovery_in=5)
    summary = DataAgent(db_session).summarize()

    reported = get_local_today() - timedelta(days=3)
    assert "ACTIVE INJURIES (CRITICAL):" in summary
    assert str(reported) in summary, "the prompt must name the day it was written"
    assert "(3d ago)" in summary
    assert "expected recovery" in summary


def test_old_injury_is_marked_stale_with_an_instruction(db_session):
    _log(db_session, days_ago=3, recovery_in=5)
    summary = DataAgent(db_session).summarize()

    assert "STALE (3d)" in summary
    # The instruction is the point: without it the model narrates the row as
    # current fact, which is exactly what shipped to the athlete.
    assert "not today" in summary
    assert "never cite this row as the reason a session was removed" in summary


def test_fresh_injury_is_not_marked_stale(db_session):
    _log(db_session, days_ago=0, recovery_in=7)
    summary = DataAgent(db_session).summarize()

    assert "(0d ago)" in summary
    assert "STALE" not in summary


def test_stale_threshold_boundary(db_session):
    _log(db_session, days_ago=INJURY_STALE_DAYS - 1)
    assert "STALE" not in DataAgent(db_session).summarize()

    db_session.query(InjuryLog).delete()
    db_session.commit()
    _log(db_session, days_ago=INJURY_STALE_DAYS)
    assert "STALE" in DataAgent(db_session).summarize()


def test_injury_without_a_report_date_still_renders(db_session):
    """Rows predate date_reported being required; they must not crash or claim
    an age they do not have."""
    db_session.add(InjuryLog(
        athlete_id=1, date_reported=None, body_part="Achilles",
        status="Active", severity=4, affected_sports="running",
    ))
    db_session.commit()

    summary = DataAgent(db_session).summarize()
    assert "Achilles" in summary
    assert "STALE" not in summary
    assert "d ago)" not in summary
