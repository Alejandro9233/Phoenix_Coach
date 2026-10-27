"""The aerobic pace bands follow where the athlete actually trains.

Week of 2026-10-26: the long-run path asked for 18.3 km and sized it at
Alex's CDMX median (6:56/km -> 127 min), but the distance stamp used the
sea-level long-run band (5:22-5:50) -> 135 min read as 24.1 km. The week
"exceeded" its 40.8 km cap on paper and the volume trim deleted three real
runs (Easy, Tempo, Recovery), leaving 33 km. Same shape on 2026-10-19.

Rules locked in here:
- The easy band centres on the median pace of the athlete's recent runs;
  recovery and long_run keep their ratios to it. Marathon, tempo and
  interval bands never move.
- Slower only: a real pace faster than the model leaves the bands alone.
- The path's minutes and the distance stamp agree on the path's km.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Activity, Athlete, Base
from backend.services.pace_enforcer import expected_run_km, km_for_minutes
from backend.services.pace_model import apply_training_pace, compute_pace_model
from backend.services.periodization_engine import PeriodizationEngine
from backend.utils.timezone import get_local_today

# Alex on 2026-10-26: COROS LT pace 4:27/km, goal 3:20, CDMX median 6:56/km.
THRESHOLD_MIN_KM = 4.45
GOAL = "3:20:00"
CDMX_PACE = 6.94  # min/km


def _model():
    return compute_pace_model(THRESHOLD_MIN_KM, GOAL, "Marathon")


def _mid(band):
    return (band["lo"] + band["hi"]) / 2


def test_easy_band_centres_on_the_real_pace():
    base = _model()
    pm = apply_training_pace(_model(), CDMX_PACE * 60)
    assert _mid(pm["bands"]["easy"]) == pytest.approx(CDMX_PACE * 60, abs=0.2)
    assert _mid(pm["bands"]["long_run"]) < _mid(pm["bands"]["easy"]) < _mid(pm["bands"]["recovery"])
    for key in ("marathon", "tempo", "interval"):
        assert pm["bands"][key] == base["bands"][key]
    assert pm["training_scale"] > 1.1


def test_a_faster_real_pace_leaves_the_bands_alone():
    base = _model()
    pm = apply_training_pace(_model(), 5.0 * 60)
    assert pm["bands"] == base["bands"]
    assert pm["training_scale"] == 1.0


def test_no_real_pace_or_no_model_is_a_no_op():
    assert apply_training_pace(_model(), None) == _model()
    assert apply_training_pace(None, 400) is None


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _run(db, day, km, pace=CDMX_PACE):
    db.add(Activity(
        id=str(uuid.uuid4()), sport="running",
        start_time=datetime.combine(day, datetime.min.time()) + timedelta(hours=7),
        distance_m=km * 1000, duration_sec=km * pace * 60, source="test",
    ))


def test_the_path_and_the_stamp_hold_the_same_km(db):
    today = get_local_today()
    monday = today - timedelta(days=today.weekday())
    db.add(Athlete(
        name="Test", race_date=today + timedelta(weeks=6, days=3),
        race_distance="Marathon", target_finish_time=GOAL,
        threshold_pace_min_km=THRESHOLD_MIN_KM,
        run_days="mon,tue,wed,thu,fri,sat,sun", swim_days="", bike_days="",
        strength_days="wed,sat", training_start_date=monday,
    ))
    for n in (3, 2, 1):
        week = monday - timedelta(weeks=n)
        for day in range(4):
            _run(db, week + timedelta(days=day), 6.6)
        _run(db, week + timedelta(days=6), 10 + 2.5 * (3 - n))
    db.commit()

    ctx = PeriodizationEngine().compute_context(db)
    vt, pm = ctx["volume_targets"], ctx["pace_model"]
    path_km = vt["long_run_path"]["km"]
    assert path_km == 18.2   # 15 km -> 27 km in two more steps (prod: 18.3 off 15.01)
    # The long run the prompt asks for stamps as the path's km, not ~30% more.
    assert km_for_minutes(vt["long_run_minutes"], pm) == pytest.approx(path_km, abs=0.2)
    # A 45-min easy run counts what 45 min at the CDMX pace covers.
    easy = {"sport": "running", "title": "Easy Run", "total_time": "45 min", "steps": []}
    km, _ = expected_run_km(easy, pm)
    assert km == pytest.approx(45 / CDMX_PACE, abs=0.2)
