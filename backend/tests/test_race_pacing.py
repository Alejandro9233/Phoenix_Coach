"""race_pacing: the deterministic race-day split table.

Rules locked in here:
- First 5K goes out FIRST_5K_EASE_SEC slower; cruise pace recovers it so the
  cumulative table still lands exactly on the target.
- Waypoints: Half + 30K + Finish for a marathon; 10K + Finish for a half.
- HR caps come from the watch's lthrZone ({index, hr}; "ratio" may be absent)
  and degrade to None on missing/malformed zones — never a crash.
- Short races get even splits (no conservative-start machinery).
"""
from backend.services.pace_model import (
    FIRST_5K_EASE_SEC, parse_hms, race_pacing,
)

ZONES = [{"index": i, "hr": hr} for i, hr in
         enumerate([120, 142, 158, 168, 178])]


def test_marathon_table_lands_on_target():
    p = race_pacing("3:10:00", "Marathon", ZONES)
    assert p["splits"][-1]["cumulative"] == "3:10:00"
    assert p["waypoints"][-1] == {"label": "Finish", "km": 42.2, "time": "3:10:00"}
    labels = [w["label"] for w in p["waypoints"]]
    assert labels == ["Half", "30K", "Finish"]
    # 8 full 5K splits + the final 2.195 km partial
    assert len(p["splits"]) == 9
    assert p["splits"][0]["to_km"] == 5.0


def test_first_5k_is_eased_and_recovered():
    p = race_pacing("3:10:00", "Marathon", None)
    avg = parse_hms("3:10:00") / 42.195

    def pace_sec(s):
        mm, rest = s.split(":")
        return int(mm) * 60 + int(rest.split("/")[0])

    first = pace_sec(p["first_5k_pace"])
    cruise = pace_sec(p["cruise_pace"])
    assert first - round(avg) in (FIRST_5K_EASE_SEC - 1, FIRST_5K_EASE_SEC,
                                  FIRST_5K_EASE_SEC + 1)
    assert cruise < first  # the ease is paid back later


def test_hr_caps_from_zones_and_guards():
    p = race_pacing("3:10:00", "Marathon", ZONES)
    assert p["hr_caps"] == {"first_10k": 142, "to_30k": 158, "final": None}

    assert race_pacing("3:10:00", "Marathon", None)["hr_caps"] is None
    assert race_pacing("3:10:00", "Marathon", [{"bogus": 1}])["hr_caps"] is None
    # Missing ratio key is fine; missing hr is skipped without crashing
    assert race_pacing("3:10:00", "Marathon",
                       [{"index": 1, "hr": 140}, {"index": 2}])["hr_caps"] is None


def test_half_marathon_waypoints():
    p = race_pacing("1:27:00", "Half Marathon", None)
    assert [w["label"] for w in p["waypoints"]] == ["10K", "Finish"]
    assert p["splits"][-1]["cumulative"] == "1:27:00"


def test_short_race_even_splits():
    p = race_pacing("0:40:00", "10k", None)
    assert p["first_5k_pace"] == p["cruise_pace"] == p["avg_pace"]


def test_degrades_to_none():
    assert race_pacing(None, "Marathon", None) is None
    assert race_pacing("garbage", "Marathon", None) is None
    assert race_pacing("3:10:00", "70.3", None) is None
