"""Hour-form durations are minutes everywhere.

2026-10-12's long run was written "1 hr 15 min". volume_gate.parse_minutes
returned None, so the gate counted the week's long run as 0 minutes
(2.3 h "planned", a spurious "longest run is 47 min" warning) and
pace_enforcer could not stamp its distance; compliance's own parser read
it as 60 and scored the 75-minute run as "125% over". One parser now, and
the normalizer rewrites hour forms to "N min" on every read and write.
"""
import pytest

from backend.services.compliance import _parse_duration_to_minutes
from backend.services.plan_normalizer import canonical_total_time, normalize_workout
from backend.services.volume_gate import parse_minutes


@pytest.mark.parametrize("text,minutes", [
    ("1 hr 15 min", 75), ("1h 15min", 75), ("1h15m", 75), ("1 hour 15 minutes", 75),
    ("1 hr", 60), ("1.5 h", 90), ("2 hrs 5 mins", 125), ("1 Hr 15 Min", 75),
    ("45 min", 45), ("45:00", 45), ("1:15:00", 75), ("0:00", 0), (75, 75),
])
def test_parse_minutes_reads_every_spelling(text, minutes):
    assert parse_minutes(text) == minutes


def test_nonsense_is_still_none():
    assert parse_minutes("a while") is None
    assert parse_minutes("") is None


def test_compliance_uses_the_same_parser():
    assert _parse_duration_to_minutes("1 hr 15 min") == 75
    assert _parse_duration_to_minutes("45 min") == 45
    assert _parse_duration_to_minutes("--") is None


def test_normalizer_rewrites_hour_forms_only():
    assert canonical_total_time("1 hr 15 min") == "75 min"
    assert canonical_total_time("1h") == "60 min"
    assert canonical_total_time("45 min") == "45 min"
    assert canonical_total_time("1:15:00") == "1:15:00"
    assert canonical_total_time("00:00") == "00:00"
    w = normalize_workout({"sport": "running", "title": "Long Run", "total_time": "1 hr 15 min",
                           "hr_target": "140-155 bpm", "steps": []})
    assert w["total_time"] == "75 min"
