"""Personal model: fitted numbers from scraped activities, never from prompt text.

Rules locked in here:
- The HR baseline needs MIN_FIT_RUNS steady outdoor runs; below that, None.
- Residuals only exist on steady runs — a race effort gets no residual.
- A refit that can't fit keeps the stored (seed) model and bumps checked_at.
- Ledger counts only easy long runs; a raced half doesn't count.
- Prediction uses the longest race-effort bucket, not the fastest 5k.
- Treadmill (no ascent) is excluded from fitting.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Activity, Athlete, Base
from backend.services import personal_model as pm


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _run(i, start, km, sec, hr, ascent=20.0, sport="running"):
    return Activity(id=f"a{i}", athlete_id=1, sport=sport, start_time=start,
                    duration_sec=sec, distance_m=km * 1000, avg_hr=hr,
                    avg_speed_ms=km * 1000 / sec, total_ascent_m=ascent, source="test")


def _steady_runs(n, base=datetime(2026, 3, 1), lthr=185):
    """Synthetic athlete: HR = 100 + 18*speed + 0.5*temp, easy pace band."""
    out = []
    for i in range(n):
        start = base + timedelta(days=i * 3)
        km = 6 + (i % 4)
        pace = 330 + (i % 5) * 12          # 5:30..6:18 /km
        sec = km * pace
        speed = 1000 / pace
        hr = 100 + 18 * speed + 0.5 * pm.month_temp(start)
        out.append(_run(i, start, km, sec, int(round(hr))))
    return out


def test_fit_needs_min_runs(db):
    runs = _steady_runs(pm.MIN_FIT_RUNS - 1)
    assert pm.fit_hr_model(runs, 185) is None
    runs = _steady_runs(pm.MIN_FIT_RUNS + 5)
    m = pm.fit_hr_model(runs, 185)
    assert m and m["n"] >= pm.MIN_FIT_RUNS
    assert abs(m["speed"] - 18) < 3          # recovers the generating slope
    assert m["rmse"] < 2


def test_residual_only_on_steady_runs():
    runs = _steady_runs(30)
    m = pm.fit_hr_model(runs, 185)
    easy = _run(99, datetime(2026, 6, 10), 8, 8 * 360, 166)   # generator: 100 + 18*2.78 + 0.5*31
    r = pm.hr_residual(m, easy, 185)
    assert r is not None and abs(r) < 6
    race = _run(98, datetime(2026, 6, 12), 10, 10 * 280, 182)   # 98% LTHR
    assert pm.hr_residual(m, race, 185) is None
    treadmill = _run(97, datetime(2026, 6, 14), 8, 8 * 360, 150, ascent=0)
    assert pm.hr_residual(m, treadmill, 185) is None


def test_residual_labels():
    assert pm.residual_label(5.0) == "Running hot"
    assert pm.residual_label(-3.5) == "Fitter than expected"
    assert pm.residual_label(0.4) == "On baseline"
    assert pm.residual_label(None) is None


def test_intensity_zones():
    assert pm.intensity(_run(1, datetime(2026, 1, 1), 5, 1800, 150), 185)["zone"] == "Z1"
    assert pm.intensity(_run(2, datetime(2026, 1, 1), 5, 1800, 170), 185)["zone"] == "Z3"
    assert pm.intensity(_run(3, datetime(2026, 1, 1), 5, 1800, 190), 185)["zone"] == "Z5"
    assert pm.intensity(_run(4, datetime(2026, 1, 1), 5, 1800, None), 185) is None


def test_ledger_counts_only_easy_long_runs():
    today = datetime(2026, 9, 8).date()
    acts = [
        _run(1, datetime(2026, 8, 1), 22, 22 * 340, 155),    # easy long
        _run(2, datetime(2026, 8, 15), 21.1, 21.1 * 281, 181),  # raced half
        _run(3, datetime(2026, 3, 1), 21, 21 * 340, 150),     # too old
        _run(4, datetime(2026, 8, 20), 15, 15 * 340, 150),    # too short
    ]
    led = pm.long_run_ledger(acts, 185, today)
    assert led["long_runs"] == 2 and led["easy_long_runs"] == 1
    assert led["last_easy_long_run"] == "2026-08-01"


def test_prediction_prefers_longest_race_effort():
    today = datetime(2026, 9, 8).date()
    acts = [
        _run(1, datetime(2025, 11, 2), 5.0, 20 * 60 + 59, 180),        # fast 5k
        _run(2, datetime(2026, 3, 15), 21.12, 98 * 60 + 54, 180),      # half 1:38:54 on 21.12 km
        _run(3, datetime(2026, 5, 1), 21.1, 124 * 60, 150),            # easy half-distance jog
    ]
    p = pm.race_prediction(acts, 185, "Marathon", "3:10:00", today)
    assert p["basis_km"] == 21.0975
    assert p["predicted"].startswith("3:2")          # ~3:26, not the 5k's 3:16
    assert p["gap_pct"] > 5
    assert pm.race_prediction(acts, None, "Marathon", "3:10:00", today) is None
    assert pm.race_prediction(acts, 185, "Olympic", "3:10:00", today) is None


def test_get_model_keeps_seed_when_refit_cannot_fit(db):
    athlete = Athlete(name="A", lthr=185, personal_model={
        "hr_model": {"intercept": 91.5, "speed": 17.6, "temp": 0.68, "ascent": 0.22, "n": 76, "rmse": 5.7, "lthr": 185},
        "lthr": 185, "lthr_source": "fit_export", "source": "fit_export",
        "checked_at": "2020-01-01T00:00:00",
    })
    db.add(athlete); db.commit()
    db.add(_run(1, datetime(2026, 8, 1), 8, 8 * 360, 150)); db.commit()
    m = pm.get_model(db, athlete)
    assert m["hr_model"]["n"] == 76 and m["source"] == "fit_export"
    assert m["checked_at"] > "2026"
    assert m["lthr"] == 185 and m["lthr_source"] == "coros"   # watch LTHR wins


def test_get_model_refits_from_scraped_runs(db):
    athlete = Athlete(name="A", lthr=185)
    db.add(athlete); db.commit()
    for a in _steady_runs(30):
        db.add(a)
    db.commit()
    m = pm.get_model(db, athlete)
    assert m["hr_model"]["n"] >= pm.MIN_FIT_RUNS and m["source"] == "scraper"
    # fresh model is not refit again
    m2 = pm.get_model(db, athlete)
    assert m2["fitted_at"] == m["fitted_at"]


def test_estimate_lthr_without_watch_value():
    acts = [_run(i, datetime(2026, 1, 1 + i), 10, 3000, 170 + i) for i in range(4)]
    assert pm.estimate_lthr(None, acts) == (173, "estimated")
    assert pm.estimate_lthr(None, acts[:2]) == (None, None)


def test_coach_lines_are_numbers(db):
    athlete = Athlete(name="A", lthr=185, race_distance="Marathon", target_finish_time="3:10:00")
    db.add(athlete); db.commit()
    for a in _steady_runs(30):
        db.add(a)
    db.add(_run(500, datetime(2026, 8, 30), 21.1, 99 * 60, 181))
    db.commit()
    lines = pm.coach_lines(db, athlete, today=datetime(2026, 9, 8).date())
    text = "\n".join(lines)
    assert "PERSONAL MODEL" in text
    assert "HR vs your baseline" in text
    assert "Long-run ledger" in text
    assert "Race prediction: Marathon" in text


# ---------------------------------------------------------------- endpoints

def test_endpoints_carry_model_fields(db):
    """Recent tab reads dashboard.personal.ledger; Profile reads prediction;
    the activity analysis merges residual + intensity over the LLM answer."""
    from fastapi.testclient import TestClient
    from backend.main import app, get_db
    from backend.agents.response_agent import ResponseAgent

    athlete = Athlete(name="A", lthr=185, race_distance="Marathon", target_finish_time="3:10:00")
    db.add(athlete); db.commit()
    for a in _steady_runs(30):
        db.add(a)
    db.add(_run(500, datetime(2026, 8, 30), 21.1, 99 * 60, 181))
    db.add(_run(501, datetime(2026, 9, 1), 22, 22 * 340, 150))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    orig = ResponseAgent.analyze_activity
    ResponseAgent.analyze_activity = lambda self, *a, **k: {"analysis": "x", "rating": "B", "advice": "y"}
    try:
        client = TestClient(app)
        dash = client.get("/dashboard").json()
        assert dash["personal"]["ledger"]["easy_long_runs"] == 1

        prof = client.get("/athlete/profile").json()
        assert prof["prediction"]["basis_km"] == 21.0975
        assert prof["prediction"]["gap_pct"] > 0

        steady = client.get("/activity/a5/analysis").json()
        assert steady["rating"] == "B"
        assert steady["intensity"]["zone"] in ("Z1", "Z2", "Z3")   # steady band is 70–95% LTHR
        assert steady["hr_residual_bpm"] is not None

        raced = client.get("/activity/a500/analysis").json()
        assert raced["hr_residual_bpm"] is None          # race effort: no residual
        assert raced["intensity"]["zone"] == "Z4"
    finally:
        ResponseAgent.analyze_activity = orig
        app.dependency_overrides.clear()
