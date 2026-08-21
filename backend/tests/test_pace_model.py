"""C1: training paces are Python-derived from measured threshold, and the
goal only ever influences the marathon band.

Rules locked in here:
- %-of-threshold bands off the watch's LT pace; easy/tempo/interval never
  move for an ambitious goal.
- Goal within 3% of fitness -> train at goal. Beyond 3% -> M-pace capped at
  0.97 x fitness pace and a note names both numbers.
- Missing data degrades (lt_only / goal_only / None), bad watch data
  (threshold outside 3:00-7:00/km) is rejected, not trained on.
- Riegel constants live HERE — predictions elsewhere must import them.
"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Athlete, Base
from backend.services.pace_model import (
    compute_pace_model,
    fmt_pace,
    parse_hms,
    riegel,
)
from backend.services.periodization_engine import PeriodizationEngine
from backend.utils.timezone import get_local_today


def test_parse_hms():
    assert parse_hms("3:10:00") == 11400
    assert parse_hms("03:10:00") == 11400
    assert parse_hms("1:27:30") == 5250
    assert parse_hms("garbage") is None
    assert parse_hms(None) is None
    assert parse_hms("3:70:00") is None


def test_goal_within_reach_anchors_on_goal():
    pm = compute_pace_model(4 + 20 / 60, "3:10:00", "Marathon")
    assert pm["anchor"] == "goal"
    assert pm["gap_pct"] < 3
    band = pm["bands"]["marathon"]
    assert band["lo"] <= 11400 / 42.195 <= band["hi"]  # goal pace inside
    assert pm["note"] is None


def test_goal_beyond_fitness_caps_mpace_and_notes_it():
    pm = compute_pace_model(4 + 40 / 60, "3:10:00", "Marathon")
    assert pm["anchor"] == "fitness"
    assert pm["gap_pct"] > 3
    fit_mp = pm["fit_mp_sec_km"]
    assert abs(pm["bands"]["marathon"]["lo"] - 0.97 * fit_mp) < 1
    assert pm["note"] is not None
    assert fmt_pace(pm["threshold_sec_km"]) in pm["note"]
    assert "3:10:00" in pm["note"]


def test_easy_pace_ignores_the_goal():
    """An ambitious goal must not speed up Tuesday's easy run."""
    modest = compute_pace_model(4.5, None, "Marathon")
    ambitious = compute_pace_model(4.5, "2:40:00", "Marathon")
    assert modest["bands"]["easy"] == ambitious["bands"]["easy"]
    assert modest["bands"]["tempo"] == ambitious["bands"]["tempo"]


def test_degradation_paths():
    goal_only = compute_pace_model(None, "3:10:00", "Marathon")
    assert goal_only["source"] == "goal_only"
    assert goal_only["bands"]["easy"]["lo"] > 0

    lt_only = compute_pace_model(4.5, None, "Marathon")
    assert lt_only["source"] == "lt_only"
    assert lt_only["note"] is None

    assert compute_pace_model(None, None, "Marathon") is None
    assert compute_pace_model(4.5, "0:45:00", "Sprint Triathlon") is None
    # 2:00/km threshold is bad watch data, not fitness.
    assert compute_pace_model(2.0, None, "Marathon") is None


def test_riegel_validates_the_house_rule():
    """1:27 half -> ~3:01 marathon (Alex's own rule of thumb)."""
    predicted = riegel(87 * 60, 21.0975, 42.195)
    assert 10860 <= predicted <= 10950


def test_sanity_bands_for_420_threshold():
    """T=4:20/km: fit marathon ~3:12, easy 5:33-6:09, tempo 4:17-4:25."""
    pm = compute_pace_model(4 + 20 / 60, None, "Marathon")
    assert abs(pm["fit_mp_sec_km"] * 42.195 - (3 * 3600 + 12 * 60)) < 60
    assert pm["bands"]["easy"]["label"] == "5:33-6:09/km"
    assert pm["bands"]["tempo"]["label"] == "4:17-4:25/km"
    assert pm["bands"]["interval"]["label"] == "4:04-4:12/km"


def test_compute_context_carries_pace_model():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(
        name="Test Athlete",
        race_date=get_local_today() + timedelta(weeks=10),
        race_distance="Marathon",
        target_finish_time="3:10:00",
        threshold_pace_min_km=4.5,
    ))
    session.commit()

    ctx = PeriodizationEngine().compute_context(session)
    assert ctx["pace_model"] is not None
    assert ctx["pace_model"]["bands"]["marathon"]["label"]

    session.close()
    engine.dispose()
