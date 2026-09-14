"""The Today ring's four segments come from ``recovery["checks"]``.

Each check is a tri-state set at the same line the engine records a concern
(council 2026-09-12): the segments can never disagree with the status word,
and missing data reads as "unknown", never as "pass".
"""
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, RecoverySnapshot
from backend.services.periodization_engine import PeriodizationEngine


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _snapshots(db, days, **overrides):
    """Seven daily rows, newest first is today. ``overrides`` apply to today only."""
    today = date.today()
    for i in range(days):
        row = dict(
            date=today - timedelta(days=i),
            hrv_ms=100.0, hrv_baseline=95.0, resting_hr=48, tib=5.0, load_ratio=0.9,
        )
        if i == 0:
            row.update(overrides)
        db.add(RecoverySnapshot(**row))
    db.commit()


def test_green_day_passes_every_check():
    db = _db()
    _snapshots(db, 7)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["status"] == "green"
    assert r["checks"] == {"hrv": "pass", "rhr": "pass", "form": "pass", "load": "pass"}


def test_one_concern_marks_only_that_check():
    db = _db()
    _snapshots(db, 7, resting_hr=53)          # +5 over the 7-day mean → slightly elevated
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["status"] == "yellow"
    assert r["checks"]["rhr"] == "concern"
    assert r["checks"]["hrv"] == "pass"
    assert r["checks"]["form"] == "pass"
    assert r["checks"]["load"] == "pass"


def test_missing_data_is_unknown_not_pass():
    db = _db()
    _snapshots(db, 1, tib=None, load_ratio=None)   # one row: no RHR trend, no form, no load
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["rhr"] == "unknown"
    assert r["checks"]["form"] == "unknown"
    assert r["checks"]["load"] == "unknown"
    assert r["checks"]["hrv"] == "unknown"        # trend needs two days of HRV


def test_no_snapshots_is_all_unknown():
    db = _db()
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["status"] == "unknown"
    assert set(r["checks"].values()) == {"unknown"}


def test_status_word_and_segments_agree():
    """Red must come with at least one concern segment; green with none."""
    db = _db()
    _snapshots(db, 7, load_ratio=1.6, tib=-22.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["status"] == "red"
    concerns = [k for k, v in r["checks"].items() if v == "concern"]
    assert set(concerns) == {"load", "form"}
