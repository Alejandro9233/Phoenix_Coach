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
    assert r["checks"] == {"hrv": "pass", "rhr": "pass", "form": "pass", "load": "pass",
                           "sleep": "unknown"}   # the fixture carries no sleep fields


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


# --- COROS's own normal range (docs/COROS_MCP.md) ---------------------------
# When a row carries hrv_normal_low/high the check follows COROS's band, not the
# stored baseline: inside the band passes even far below the baseline (a new
# altitude), below the band is a concern even one day in.

def test_inside_coros_band_passes_despite_low_baseline_pct():
    db = _db()
    _snapshots(db, 7, hrv_ms=70.0, hrv_baseline=95.0, hrv_normal_low=58.0, hrv_normal_high=97.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["hrv"] == "pass"            # −26% vs baseline would have flagged before
    assert r["hrv_vs_baseline"] == "-26%"          # the tile still shows the honest percentage


def test_below_coros_band_is_a_concern_with_the_range_in_words():
    db = _db()
    _snapshots(db, 7, hrv_ms=47.0, hrv_baseline=77.0, hrv_normal_low=58.0, hrv_normal_high=97.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["hrv"] == "concern"
    assert "below your COROS normal range (58–97 ms)" in r["detail"]


def test_rows_without_a_band_keep_the_legacy_trend_rule():
    db = _db()
    _snapshots(db, 7, hrv_ms=80.0)                 # 3-day avg (80,100,100)=93 vs 95 → −2%: pass
    assert PeriodizationEngine()._get_recovery_status(db)["checks"]["hrv"] == "pass"


# --- Sleep (COROS MCP data) and sleeping HR ---------------------------------

def test_sleep_unknown_when_the_row_has_no_sleep_fields():
    db = _db()
    _snapshots(db, 7)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["sleep"] == "unknown" and r["sleep"] is None and r["sleep_hr"] is None


def test_good_sleep_passes_and_is_reported():
    db = _db()
    _snapshots(db, 7, sleep_duration_hr=8.0, sleep_quality_score=82.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["sleep"] == "pass" and r["sleep"] == {"hours": 8.0, "score": 82.0}
    assert r["status"] == "green"


def test_short_sleep_is_a_concern_and_turns_the_day_yellow():
    db = _db()
    _snapshots(db, 7, sleep_duration_hr=5.5, sleep_quality_score=80.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["sleep"] == "concern" and r["status"] == "yellow"
    assert "Short sleep — 5.5 h last night" in r["detail"]


def test_poor_sleep_score_is_a_concern():
    db = _db()
    _snapshots(db, 7, sleep_duration_hr=8.5, sleep_quality_score=55.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["checks"]["sleep"] == "concern" and "COROS score 55" in r["detail"]


def _snapshots_with_sleep_hr(db, today_min, history_min, days=10):
    today = date.today()
    for i in range(days):
        db.add(RecoverySnapshot(date=today - timedelta(days=i), hrv_ms=100.0, hrv_baseline=95.0,
                                resting_hr=48, tib=5.0, load_ratio=0.9,
                                sleep_hr_min=today_min if i == 0 else history_min,
                                sleep_hr_avg=(today_min + 8) if i == 0 else (history_min + 8)))
    db.commit()


def test_sleeping_hr_readout_against_its_30_day_norm():
    db = _db()
    _snapshots_with_sleep_hr(db, today_min=42.0, history_min=41.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["sleep_hr"] == {"min": 42.0, "avg": 50.0, "norm_30d": 41.0, "delta": 1.0}
    assert r["status"] == "green"


def test_sleeping_hr_well_above_norm_is_called_out():
    db = _db()
    _snapshots_with_sleep_hr(db, today_min=46.0, history_min=41.0)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["sleep_hr"]["delta"] == 5.0
    assert "Sleeping HR 46 bpm — 5 above your 30-day norm" in r["detail"] and r["status"] == "yellow"


def test_sleeping_hr_needs_a_week_of_history_for_a_norm():
    db = _db()
    _snapshots_with_sleep_hr(db, today_min=46.0, history_min=41.0, days=4)
    r = PeriodizationEngine()._get_recovery_status(db)
    assert r["sleep_hr"]["norm_30d"] is None and r["sleep_hr"]["delta"] is None and r["status"] == "green"
