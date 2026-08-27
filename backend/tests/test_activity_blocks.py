"""Pin the COROS detail decoder to the verified 2026-08-25 tempo run.

Every expected number here was read off the COROS web UI for that activity
and cross-checked against the raw payload (three independent verification
passes). If a change breaks one of these, the change is wrong — not the test.
"""

import json
import os

import pytest

from backend.services.activity_blocks import (
    decode_activity, fmt_pace, fmt_time, render_report)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "tempo_run_detail.json")


@pytest.fixture(scope="module")
def report():
    with open(FIXTURE) as f:
        return decode_activity(json.load(f))


def test_formatters():
    assert fmt_time(2412.95) == "40:13"      # rounds like the UI
    assert fmt_time(720.00) == "12:00"
    assert fmt_time(10.10) == "0:10"
    assert fmt_time(3725) == "1:02:05"
    assert fmt_pace(341.29) == "5'41\""
    assert fmt_pace(429.75) == "7'10\""      # nearest second, not truncation
    assert fmt_pace(730.3) == "12'10\""


def test_totals_match_the_ui(report):
    t = report["totals"]
    assert t["name"] == "tempo run"
    assert t["distance_km"] == pytest.approx(7.07001)
    assert t["moving_s"] == pytest.approx(2412.95)   # 40:12.95
    assert t["elapsed_s"] == pytest.approx(2464.26)  # 41:04.26
    assert t["pause_s"] == pytest.approx(51.31)      # summary value, not pauseList's 52.06
    assert fmt_pace(t["pace_s_per_km"]) == "5'41\""
    assert t["avg_hr"] == 170 and t["max_hr"] == 200
    assert t["avg_cadence"] == 158 and t["avg_power"] == 234
    assert t["calories_kcal"] == 733
    assert t["training_load"] == 142
    assert t["aerobic_effect"] == pytest.approx(3.0)
    assert t["anaerobic_effect"] == pytest.approx(2.7)
    assert t["vo2max"] == 58
    assert t["best_km_s_per_km"] == 260                # pre-truncated by the watch
    assert t["standard_rate"] == pytest.approx(0.376)
    assert t["temp_c"] == pytest.approx(37.8)
    assert t["humidity_pct"] == pytest.approx(36.0)
    assert t["start_local"].strftime("%Y-%m-%d %H:%M") == "2026-08-25 19:56"


def test_blocks_match_the_ui(report):
    blocks = report["blocks"]
    assert [b["name"] for b in blocks] == ["Warm Up", "Run", "Cool Down", "Run"]
    assert [round(b["distance_km"], 2) for b in blocks] == [1.68, 4.37, 1.01, 0.01]
    assert [fmt_time(b["time_s"]) for b in blocks] == \
        ["12:00", "20:03", "8:00", "0:10"]
    assert [fmt_pace(b["pace_s_per_km"]) for b in blocks] == \
        ["7'10\"", "4'35\"", "7'54\"", "12'10\""]
    assert [b["avg_hr"] for b in blocks] == [138, 188, 173, 168]
    assert [b["avg_cadence"] for b in blocks] == [149, 165, 152, 152]
    assert [b["avg_power"] for b in blocks] == [182, 289, 173, 171]
    assert [b["standard_rate"] for b in blocks] == [1.0, 0.153, 0.0, None]


def test_prescriptions_decode(report):
    warm, main, cool, trailing = report["blocks"]
    assert warm["target_s"] == 720
    assert warm["intensity"] == {"kind": "hr_below", "bpm": 141}
    assert warm["ended"] == "timer"          # 72000 cs == 720 s exactly

    assert main["target_s"] == 2100          # 35:00 prescribed...
    assert main["intensity"]["kind"] == "pace_range"
    assert main["intensity"]["low_s_per_km"] == pytest.approx(266.0)   # 4'26"
    assert main["intensity"]["high_s_per_km"] == pytest.approx(270.0)  # 4'30"
    assert main["ended"] == "lap_key"        # ...cut at 20:02.84

    assert cool["target_s"] == 480 and cool["ended"] == "timer"

    assert trailing["is_program"] is False   # programExerciseIndex 255
    assert trailing["target_s"] is None
    assert trailing["standard_rate"] is None  # -1 sentinel decoded away


def test_blocks_sum_to_totals(report):
    blocks, t = report["blocks"], report["totals"]
    assert sum(b["distance_km"] for b in blocks) == pytest.approx(t["distance_km"])
    # Known 1-centisecond seam between block sum and the whole activity.
    assert sum(b["time_s"] for b in blocks) == pytest.approx(t["moving_s"], abs=0.02)


def test_splits_restart_per_block(report):
    by_block = {}
    for ex, s in report["splits"]:
        by_block.setdefault(ex, []).append(s)
    assert {ex: len(rows) for ex, rows in by_block.items()} == {1: 2, 2: 5, 3: 2, 4: 1}
    # Km numbering restarts inside each block.
    assert [s["km_in_block"] for s in by_block[2]] == [1, 2, 3, 4, 5]
    # Paces are recomputed time/distance, dodging the avgMoveSpeed trap.
    fastest = min(by_block[2], key=lambda s: s["pace_s_per_km"])
    assert fastest["pace_s_per_km"] == pytest.approx(260.87, abs=0.01)
    # Partial rows are flagged (0.68 km warm-up remainder etc).
    assert by_block[1][1]["partial"] and not by_block[1][0]["partial"]


def test_render_is_pasteable(report):
    text = render_report(report)
    assert "TEMPO RUN" in text
    assert "1.68 km" in text and "4.37 km" in text
    assert "prescribed 35:00 at 4'26\"–4'30\"/km — ended early by lap key at 20:03" in text
    assert "prescribed 12:00, HR ≤ 141 bpm — completed" in text
    assert "in range 100%" in text and "in range 15%" in text and "in range 0%" in text
    assert "after the program ended" in text
    assert "weather 37.8°C" in text
    # The trailing lap renders with a + marker, not a block number.
    assert "\n +  Run" in text


def test_free_run_laps_are_not_called_blocks():
    """A free run's auto-km laps arrive as type-2 items with no target —
    the section must say LAPS, with no prescription lines."""
    payload = _fixture_payload()
    for group in payload["data"]["lapList"]:
        for item in group["lapItemList"]:
            for key in ("targetValue", "intensityType", "intensityValue",
                        "intensityValueExtend", "intensityMultiplier"):
                item.pop(key, None)
    text = render_report(decode_activity(payload))
    assert "\nLAPS\n" in text and "BLOCKS" not in text
    assert "prescribed" not in text


def test_non_running_sports_skip_the_lap_table():
    """Strength records sets/rests as dozens of 0.00 km laps — show totals
    plus a note, never the meaningless lap table."""
    payload = _fixture_payload()
    payload["data"]["summary"]["sportType"] = 402
    text = render_report(decode_activity(payload))
    assert "lap detail is decoded for runs and rides only" in text
    assert "BLOCKS" not in text and "KM SPLITS" not in text
    assert "FORM" not in text


def _fixture_payload():
    with open(FIXTURE) as f:
        return json.load(f)


def test_missing_optional_sections_do_not_crash():
    payload = _fixture_payload()
    data = payload["data"]
    data.pop("weather", None)
    data.pop("sportFeelInfo", None)
    data.pop("deviceList", None)
    data["lapList"] = [g for g in data["lapList"] if g["type"] == -1]
    report = decode_activity(payload)
    assert report["blocks"] == [] and report["splits"] == []
    text = render_report(report)
    assert "TEMPO RUN" in text
    assert "BLOCKS" not in text        # no empty section header
    assert "weather" not in text


def test_free_run_keeps_its_splits():
    """No programmed blocks (a free run) must still show the km splits."""
    payload = _fixture_payload()
    payload["data"]["lapList"] = [
        g for g in payload["data"]["lapList"] if g["type"] != 2]
    text = render_report(decode_activity(payload))
    assert "BLOCKS" not in text
    assert "KM SPLITS" in text
    assert "4'21\"" in text            # the fastest km survives


def test_sensorless_blocks_render_without_none():
    """Missing HR/cadence/dynamics must drop segments, never print None."""
    payload = _fixture_payload()
    data = payload["data"]
    for key in ("avgHr", "maxHr", "avgCadence", "aerobicEffect",
                "anaerobicEffect"):
        data["summary"].pop(key, None)
    for group in data["lapList"]:
        for item in group["lapItemList"]:
            for key in ("avgHr", "maxHr", "avgCadence", "strideHeight",
                        "avgStrideLength"):
                item.pop(key, None)
    text = render_report(decode_activity(payload))
    assert "None" not in text
    assert "bpm avg" not in text and " spm" not in text
    # The prescription's "HR ≤ 141 bpm" stays — it's the plan, not sensor data.
    assert "prescribed 12:00, HR ≤ 141 bpm" in text
    assert "vert osc -/-/- cm" in text  # dynamics gone, FORM stays aligned


def test_aerobic_without_anaerobic_renders():
    payload = _fixture_payload()
    payload["data"]["summary"].pop("anaerobicEffect", None)
    text = render_report(decode_activity(payload))
    assert "aerobic 3.0" in text and "anaerobic" not in text


def test_error_payload_raises_valueerror():
    with pytest.raises(ValueError, match="Service exceptions"):
        decode_activity({"result": "1001", "data": None,
                         "message": "Service exceptions"})


# ─── rides (fixture: the 2026-08-26 "Endurance Ride (Z2)") ────────────────────

RIDE_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                            "endurance_ride_detail.json")


@pytest.fixture(scope="module")
def ride():
    with open(RIDE_FIXTURE) as f:
        return decode_activity(json.load(f))


def test_ride_totals_speak_kmh(ride):
    t = ride["totals"]
    assert t["is_ride"] and not t["is_run"]
    assert t["speed_kmh"] == pytest.approx(26.40)
    assert t["pace_s_per_km"] is None      # avgSpeed is km/h x100 on rides
    assert t["distance_km"] == pytest.approx(42.8899)


def test_ride_blocks_decode(ride):
    warm, main = ride["blocks"][:2]
    assert warm["target_s"] == 900 and warm["ended"] == "timer"
    assert warm["intensity"] == {"kind": "hr_below", "bpm": 141}
    assert main["target_s"] == 6300 and main["ended"] == "lap_key"
    # Z2 prescribes a band, not a cap.
    assert main["intensity"] == {"kind": "hr_range",
                                 "low_bpm": 142, "high_bpm": 159}
    assert main["standard_rate"] == pytest.approx(1.0)
    assert main["speed_kmh"] == pytest.approx(26.96, abs=0.01)


def test_ride_renders_as_a_ride(ride):
    text = render_report(ride)
    assert "26.4 km/h avg" in text
    assert "/km" not in text               # no run pace anywhere
    assert "prescribed 15:00, HR ≤ 141 bpm — completed" in text
    assert "prescribed 1:45:00, HR 142–159 bpm — ended early by lap key" in text
    assert "in range 100%" in text
    assert "FORM" not in text              # run-only metrics stay out
