"""Per-workout distance compliance.

Plans carry a numeric distance_km per workout (prompt contract + normalizer);
compliance compares it against the scraped activity. Rules locked in here:
- distance feeds score and notes, never `status` (duration/HR own that);
- a plan without distance_km (legacy weeks) skips the check silently;
- distance_pct is exposed for future consumers (run-km progress bar).
"""
from backend.services.compliance import _compute_workout_compliance


def _run(planned_extra=None, actual_extra=None):
    planned = {"sport": "running", "title": "Easy Run", "total_time": "60 min",
               "hr_target": "140-150 bpm"}
    actual = {"duration_sec": 3600, "avg_hr": 145, "distance_m": 10000}
    planned.update(planned_extra or {})
    actual.update(actual_extra or {})
    return _compute_workout_compliance(planned, actual)


def test_distance_on_plan_scores_and_notes():
    result = _run(planned_extra={"distance_km": 10.0})
    assert result["distance_pct"] == 100
    assert "Distance 10.0 km — on plan" in result["notes"]
    assert result["status"] == "completed"


def test_distance_under_plan_noted_but_status_unchanged():
    result = _run(planned_extra={"distance_km": 14.0})
    assert result["distance_pct"] == 71
    assert "under" in result["notes"]
    # Duration and HR are on target — a short-but-honest session must not
    # read as a mismatch.
    assert result["status"] == "completed"


def test_legacy_plan_without_distance_skips_check():
    result = _run()
    assert result["distance_pct"] is None
    assert "Distance" not in result["notes"]


def test_zero_actual_distance_skips_check():
    # e.g. a strength session matched against a plan that carried distance.
    result = _run(planned_extra={"distance_km": 10.0},
                  actual_extra={"distance_m": None})
    assert result["distance_pct"] is None
