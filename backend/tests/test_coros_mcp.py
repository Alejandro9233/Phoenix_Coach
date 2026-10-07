"""COROS MCP client — parsers, derivations, guards, scraper-shaped payload.

The MCP answers in prose, so the parser is the only piece that can be wrong
silently. Fixtures mirror the exact templates sampled 2026-10-07 (values are
invented). The council's rules under test: a missing or reworded line yields
None (never 0), write tools are refused, and the MCP path never reaches
COROS unless explicitly enabled.
"""
import os
from datetime import date
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.services import coros_mcp as mcp

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


# ---------------------------------------------------------------- transport

def test_tool_text_unwraps_json_string_literal_and_objects():
    prose = mcp.tool_text({"content": [{"type": "text", "text": '"Line one\\nLine two"'}]})
    assert prose == "Line one\nLine two"
    obj = mcp.tool_text({"content": [{"type": "text", "text": '{"a": 1}'}]})
    assert obj == '{"a": 1}'
    raw = mcp.tool_text({"content": [{"type": "text", "text": "plain, not json"}]})
    assert raw == "plain, not json"


def test_tool_text_raises_on_is_error():
    with pytest.raises(mcp.CorosMcpError):
        mcp.tool_text({"isError": True, "content": [{"type": "text", "text": "nope"}]})


def test_write_tools_are_refused_before_any_network():
    client = mcp.McpClient({"issuer": "https://mcpus.coros.com", "access_token": "x"})
    for name in ("createScheduledWorkout", "updateTrainingPlan", "scheduleWorkout",
                 "createSingleWorkout", "updateWorkoutDetails"):
        with pytest.raises(mcp.CorosMcpError, match="read-only"):
            client.call(name, {})
    assert client.calls == 0


# ---------------------------------------------------------------- parsers

def test_parse_sleep_hrv_fixture():
    rows = mcp.parse_sleep_hrv(fixture("coros_mcp_sleep_hrv.txt"))
    assert set(rows) == {date(2026, 3, d) for d in range(3, 9)}  # time series dates ignored
    assert rows[date(2026, 3, 8)] == {
        "hrv_ms": 52, "hrv_eval": "Below normal",
        "hrv_normal_low": 61, "hrv_normal_high": 99, "hrv_baseline": 80,
    }
    assert rows[date(2026, 3, 6)]["hrv_eval"] == "Low"
    assert rows[date(2026, 3, 5)]["hrv_eval"] == "Above normal"
    assert rows[date(2026, 3, 5)]["hrv_ms"] == 105


def test_parse_sleep_hrv_time_series_never_overwrites_assessment():
    # 2026-03-03 appears in both sections; the assessment wins.
    rows = mcp.parse_sleep_hrv(fixture("coros_mcp_sleep_hrv.txt"))
    assert rows[date(2026, 3, 3)]["hrv_ms"] == 69
    # 2026-03-02 exists only in the time series → not an assessment row.
    assert date(2026, 3, 2) not in rows


def test_parse_resting_hr_no_data_is_none():
    rows = mcp.parse_resting_hr(fixture("coros_mcp_resting_hr.txt"))
    assert rows[date(2026, 3, 8)] == 54
    assert rows[date(2026, 3, 6)] is None
    assert len(rows) == 7


def test_parse_training_load_fixture():
    rows = mcp.parse_training_load(fixture("coros_mcp_training_load.txt"))
    assert rows[date(2026, 3, 8)] == {"ati": 46, "cti": 60, "load_ratio": 0.76,
                                      "comment": "Performance"}
    assert rows[date(2026, 3, 4)]["load_ratio"] == 1.87
    assert len(rows) == 5


def test_reworded_prose_yields_none_not_zero():
    rows = mcp.parse_training_load(fixture("coros_mcp_training_load_reworded.txt"))
    day = rows[date(2026, 3, 8)]
    assert day["ati"] is None and day["cti"] is None and day["load_ratio"] is None
    assert day["comment"] == "Performance"  # the line that didn't change still parses
    merged = mcp.gate_rows({}, {}, rows)
    row = merged[date(2026, 3, 8)]
    assert row["tib"] is None and row["fatigue_state"] is None
    assert set(mcp.missing_gate_fields(row)) == set(mcp.GATE_FIELDS)


def test_partial_block_leaves_only_the_missing_field_none():
    text = "Training Load Assessment\n====\n\n2026-03-08\nComment: Performance\nShort-Term Load: 46\nLoad Ratio: 0.76\n"
    day = mcp.parse_training_load(text)[date(2026, 3, 8)]
    assert day == {"ati": 46, "cti": None, "load_ratio": 0.76, "comment": "Performance"}


# ---------------------------------------------------------------- derivations
# Truths from samples/coros_mcp/scraper_raw_20261007.json (2026-10-07 row):
# ati 46, cti 60, tib 14, tiredRateNew -14, tiredRateStateNew 2,
# trainingLoadRatio 0.76, trainingLoadRatioState 2; Monday 10-05 cti 61 →
# recomendTlMin 427.0, recomendTlMax 768.6.

def test_derivations_match_scraper_truths():
    d = mcp.derive_load_fields(46, 60, 0.76)
    assert d == {"tib": 14.0, "fatigue_pct": -14.0, "fatigue_state": 2, "load_ratio_state": 2}
    assert mcp.recommended_band(61) == (427.0, 768.6)


def test_derivations_propagate_none():
    assert mcp.derive_load_fields(None, 60, 0.76) == {
        "tib": None, "fatigue_pct": None, "fatigue_state": None, "load_ratio_state": 2}
    assert mcp.derive_load_fields(46, 60, None)["load_ratio_state"] is None
    assert mcp.recommended_band(None) is None
    assert mcp.fatigue_state_for(-14, None) is None


def test_fatigue_state_zone_edges_at_cti_60():
    # zones of tiredRateNew (= -tib): [-inf,-30)=1 [-30,-6)=2 [-6,3)=3 [3,48)=4 [48,inf)=5
    assert mcp.fatigue_state_for(-70, 60) == 1
    assert mcp.fatigue_state_for(-30, 60) == 2   # lower bound inclusive
    assert mcp.fatigue_state_for(-6, 60) == 3
    assert mcp.fatigue_state_for(2.9, 60) == 3
    assert mcp.fatigue_state_for(3, 60) == 4     # the gate's "fatigue_high" edge
    assert mcp.fatigue_state_for(48, 60) == 5


def test_load_ratio_state_zones():
    assert [mcp.load_ratio_state_for(r) for r in (0.1, 0.5, 0.79, 0.8, 1.0, 1.49, 1.5, 1.87)] \
        == [1, 2, 2, 3, 4, 4, 5, 5]


def test_gate_rows_merge_by_date_and_derive():
    hrv = mcp.parse_sleep_hrv(fixture("coros_mcp_sleep_hrv.txt"))
    rhr = mcp.parse_resting_hr(fixture("coros_mcp_resting_hr.txt"))
    load = mcp.parse_training_load(fixture("coros_mcp_training_load.txt"))
    rows = mcp.gate_rows(hrv, rhr, load)
    row = rows[date(2026, 3, 8)]
    assert row["hrv_ms"] == 52 and row["resting_hr"] == 54 and row["ati"] == 46
    assert row["tib"] == 14.0 and row["fatigue_state"] == 2 and row["load_ratio_state"] == 2
    assert mcp.missing_gate_fields(row) == []
    # 03-02 has only an RHR → every other gate field missing, nothing invented
    assert mcp.missing_gate_fields(rows[date(2026, 3, 2)]) == [
        f for f in mcp.GATE_FIELDS if f != "resting_hr"]


# ---------------------------------------------------------------- compare / report

def _snapshot(**kw):
    base = dict(hrv_ms=52.0, hrv_baseline=80.0, resting_hr=54, ati=46.0, cti=60.0,
                tib=14.0, fatigue_pct=-14.0, fatigue_state=2, load_ratio=0.76,
                load_ratio_state=2)
    base.update(kw)
    return SimpleNamespace(**base)


def _row(**kw):
    """A parsed gate row; base-field overrides apply BEFORE derivation so
    ati=None really does take tib and fatigue_state down with it."""
    base = {"hrv_ms": 52, "hrv_baseline": 80, "resting_hr": 54, "ati": 46, "cti": 60,
            "load_ratio": 0.76, "hrv_eval": "Below normal", "load_comment": "Performance"}
    base.update({k: v for k, v in kw.items() if k in base})
    row = dict(base)
    row.update(mcp.derive_load_fields(row["ati"], row["cti"], row["load_ratio"]))
    row.update({k: v for k, v in kw.items() if k not in base})
    return row


def test_compare_exact_match_tolerates_float_rounding():
    cmp = mcp.compare_with_snapshot(_row(), _snapshot(load_ratio=0.7649, tib=14.3))
    assert cmp["mismatches"] == [] and cmp["mcp_missing"] == [] and cmp["db_missing"] == []
    assert all(v["match"] for v in cmp["fields"].values())


def test_compare_flags_mismatch_and_missing_on_both_sides():
    cmp = mcp.compare_with_snapshot(_row(resting_hr=None, hrv_ms=47),
                                    _snapshot(fatigue_state=None))
    assert cmp["mismatches"] == ["hrv_ms"]
    assert cmp["mcp_missing"] == ["resting_hr"]
    assert cmp["db_missing"] == ["fatigue_state"]
    assert cmp["fields"]["hrv_ms"] == {"mcp": 47, "db": 52.0, "match": False}


def test_compare_with_no_snapshot_marks_everything_db_missing():
    cmp = mcp.compare_with_snapshot(_row(), None)
    assert set(cmp["db_missing"]) == set(mcp.COMPARE_FIELDS)
    assert cmp["mismatches"] == []


def test_shadow_report_statuses():
    today = date(2026, 3, 8)
    fetch = {"rows": {today: _row()}, "calls": 4, "elapsed_ms": 1200}
    ok = mcp.shadow_report(today, _snapshot(), fetch=fetch)
    assert ok["status"] == "ok" and ok["missing"] == [] and ok["history_days"] == 1
    assert ok["hrv_eval"] == "Below normal" and ok["calls"] == 4

    diff = mcp.shadow_report(today, _snapshot(hrv_ms=60.0), fetch=fetch)
    assert diff["status"] == "diff" and diff["mismatches"] == ["hrv_ms"]

    missing = mcp.shadow_report(today, _snapshot(), fetch={"rows": {today: _row(ati=None)}})
    assert missing["status"] == "diff" and "ati" in missing["mcp_missing"]
    assert "tib" in missing["missing"]  # derived from the missing ati

    no_snap = mcp.shadow_report(today, None, fetch=fetch)
    assert no_snap["status"] == "no_snapshot" and no_snap["snapshot_present"] is False
    assert set(no_snap["db_missing"]) == set(mcp.COMPARE_FIELDS)

    no_row = mcp.shadow_report(today, _snapshot(), fetch={"rows": {date(2026, 3, 7): _row()}})
    assert no_row["status"] == "no_today_row" and no_row["missing"] == list(mcp.GATE_FIELDS)

    err = mcp.shadow_report(today, None, error="CorosMcpError: token refresh failed")
    assert err["status"] == "error" and err["reason"].startswith("CorosMcpError")
    assert err["snapshot_present"] is False


# ---------------------------------------------------------------- enablement / tokens

def test_disabled_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("COROS_MCP_TOKEN_ROOT", str(tmp_path))
    monkeypatch.delenv("COROS_MCP_TOKEN_PATH", raising=False)
    monkeypatch.delenv("COROS_MCP_ENABLED", raising=False)
    assert mcp.find_token_path() is None
    assert mcp.enabled() is False
    with pytest.raises(mcp.CorosMcpError, match="no COROS MCP token"):
        mcp.ensure_token()


def test_enabled_with_token_and_off_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("COROS_MCP_TOKEN_ROOT", str(tmp_path))
    monkeypatch.delenv("COROS_MCP_TOKEN_PATH", raising=False)
    monkeypatch.delenv("COROS_MCP_ENABLED", raising=False)
    path = mcp.save_token({"issuer": "https://mcpus.coros.com", "client_id": "c",
                           "access_token": "a", "refresh_token": "r",
                           "expires_at": 9_999_999_999, "obtained_at": 0})
    assert path == tmp_path / "us" / "token.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert mcp.enabled() is True
    assert mcp.ensure_token()["access_token"] == "a"  # fresh → no refresh call
    monkeypatch.setenv("COROS_MCP_ENABLED", "0")
    assert mcp.enabled() is False


def test_refresh_keeps_old_refresh_token_when_omitted(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "{}"
        def json(self):
            return {"access_token": "new", "expires_in": 100}
    monkeypatch.setattr(mcp.requests, "post", lambda *a, **k: FakeResp())
    new = mcp.refresh_token({"issuer": "https://mcpus.coros.com", "client_id": "c",
                             "access_token": "old", "refresh_token": "keep-me"})
    assert new["access_token"] == "new" and new["refresh_token"] == "keep-me"


def test_refresh_without_refresh_token_asks_for_login():
    with pytest.raises(mcp.CorosMcpError, match="run login again"):
        mcp.refresh_token({"issuer": "x", "client_id": "c", "access_token": "a"})


# ---------------------------------------------------------------- live-sync parsers

def test_parse_sport_records_fixture():
    recs = mcp.parse_sport_records(fixture("coros_mcp_sport_records.txt"))
    assert [r["labelId"] for r in recs] == ["900000000000000001", "900000000000000002", "900000000000000003"]
    run, strength, ride = recs
    assert run == {"sport": "Indoor Run", "date": date(2026, 3, 8), "name": "Easy run",
                   "start": 1772996400, "end": 1772998500, "duration_s": 2100, "distance_km": 4.89,
                   "pace_s": 429, "avg_hr": 151, "calories": 396, "sets": None,
                   "labelId": "900000000000000001", "sportType": 101}
    assert strength["sets"] == 25 and strength["duration_s"] == 4205 and strength["distance_km"] is None
    assert strength["avg_hr"] == 101  # the odd line break before "| Avg HR" doesn't lose it
    assert ride["sportType"] == 200 and ride["distance_km"] == 35.92 and ride["pace_s"] is None


def test_parse_activity_detail_fixture():
    d = mcp.parse_activity_detail(fixture("coros_mcp_activity_detail.txt"))
    assert d["training_load"] == 234 and d["cadence"] == 153 and d["stride_m"] == 0.95
    assert d["power_w"] == 185 and d["elevation_gain_m"] == 6 and d["calories"] == 1276
    assert d["avg_hr"] == 164 and d["aerobic_te"] == 4.0 and d["anaerobic_te"] == 0
    assert d["focus"] == "Base" and d["performance"] == "Below Average"


def test_parse_sleep_overview_stress_fitness_recovery_user():
    sl = mcp.parse_sleep_overview(fixture("coros_mcp_sleep_overview.txt"))
    assert sl[date(2026, 3, 8)] == {"sleep_score": 82, "main_sleep_min": 500, "daily_sleep_min": 524,
                                    "deep_pct": 19, "rem_pct": 11, "awake_min": 6, "naps_min": 24}
    assert sl[date(2026, 3, 7)]["main_sleep_min"] == 338
    st = mcp.parse_stress_level(fixture("coros_mcp_stress_level.txt"))
    assert st == {date(2026, 3, 8): 30, date(2026, 3, 7): 41}
    fit = mcp.parse_fitness_overview(fixture("coros_mcp_fitness_overview.txt"))
    assert fit == {"vo2max": 55, "running_level": 79, "threshold_pace_s": 280, "pred_5k_s": 1330,
                   "pred_10k_s": 2760, "pred_half_s": 6210, "pred_marathon_s": 13440}
    rec = mcp.parse_recovery_status(fixture("coros_mcp_recovery_status.txt"))
    assert rec == {"recovery_pct": 88, "level": "Heavy training allowed", "full_recovery_h": 14}
    assert mcp.parse_user_info(fixture("coros_mcp_user_info.txt"))["weight_kg"] == 71.3


def test_bounds_turn_implausible_values_into_none():
    rows = mcp.gate_rows({date(2026, 3, 8): {"hrv_ms": 999, "hrv_baseline": 80}},
                         {date(2026, 3, 8): 54}, {date(2026, 3, 8): {"ati": 46, "cti": 60, "load_ratio": 0.76}})
    row = rows[date(2026, 3, 8)]
    assert row["hrv_ms"] is None and row["hrv_baseline"] == 80
    assert mcp.missing_gate_fields(row) == ["hrv_ms"]


# ---------------------------------------------------------------- scraper-shaped payload

def _parsed_fixtures():
    return dict(
        records=mcp.parse_sport_records(fixture("coros_mcp_sport_records.txt")),
        sleep_hrv=mcp.parse_sleep_hrv(fixture("coros_mcp_sleep_hrv.txt")),
        resting_hr=mcp.parse_resting_hr(fixture("coros_mcp_resting_hr.txt")),
        load=mcp.parse_training_load(fixture("coros_mcp_training_load.txt")),
        sleep=mcp.parse_sleep_overview(fixture("coros_mcp_sleep_overview.txt")),
        stress=mcp.parse_stress_level(fixture("coros_mcp_stress_level.txt")),
        fitness=mcp.parse_fitness_overview(fixture("coros_mcp_fitness_overview.txt")),
        recovery=mcp.parse_recovery_status(fixture("coros_mcp_recovery_status.txt")),
        user=mcp.parse_user_info(fixture("coros_mcp_user_info.txt")),
    )


def test_build_scrape_payload_shapes_like_the_scraper():
    f = _parsed_fixtures()
    details = {f["records"][0]["labelId"]: mcp.parse_activity_detail(fixture("coros_mcp_activity_detail.txt"))}
    payload = mcp.build_scrape_payload(date(2026, 3, 8), "America/Mexico_City", f["records"], details,
                                       f["sleep_hrv"], f["resting_hr"], f["load"], sleep=f["sleep"],
                                       stress=f["stress"], fitness=f["fitness"], recovery=f["recovery"], user=f["user"])
    assert payload["source"] == "coros_mcp" and payload["today_status"] == "ok" and payload["missing"] == []
    day = next(d for d in payload["evolab"]["analyse_query"]["dayList"] if d["happenDay"] == 20260308)
    assert day["avgSleepHrv"] == 52 and day["sleepHrvBase"] == 80 and day["testRhr"] == 54
    assert day["ati"] == 46 and day["cti"] == 60 and day["tib"] == 14.0 and day["tiredRateNew"] == -14.0
    assert day["tiredRateStateNew"] == 2 and day["trainingLoadRatio"] == 0.76 and day["trainingLoadRatioState"] == 2
    assert day["sleepScore"] == 82 and day["sleepDurationMin"] == 500 and day["stressAvg"] == 30
    assert day["vo2max"] == 55 and day["staminaLevel"] == 79 and day["ltsp"] == 280
    # keys the MCP can't supply are ABSENT, so ingestion leaves the columns alone
    for absent in ("trainingLoad", "lthr", "t7d", "sleepHrvSd", "recomendTlMin"):
        assert absent not in day
    si = payload["evolab"]["dashboard_query"]["summaryInfo"]
    assert si["recoveryPct"] == 88
    assert {"happenDay": 20260308, "avgSleepHrv": 52, "sleepHrvBase": 80} in si["sleepHrvData"]["sleepHrvList"]
    assert payload["evolab"]["mcp_user_profile"] == {"weight": 71.3}
    acts = {a["labelId"]: a for a in payload["activities"]}
    run = acts["900000000000000001"]
    assert run["distance"] == 4890.0 and run["duration"] == 2100 and run["avgSpeed"] == round(2100 / 4.89, 2)
    assert run["trainingLoad"] == 234 and run["pitch"] == 153 and run["avgPower"] == 185
    assert run["name"] == "Easy run" and run["calories"] == 396 and run["source"] == "coros_mcp"
    assert run["avgHeartRate"] == 151 and run["sportType"] == 101
    strength = acts["900000000000000002"]
    assert strength["sets"] == 25 and strength["distance"] == 0 and strength["avgSpeed"] == 0
    assert strength["trainingLoad"] == 0 and strength["pitch"] is None  # no detail fetched for it
    assert acts["900000000000000003"]["avgSpeed"] == round(4732 / 35.92, 2)


def test_recommended_band_comes_from_the_previous_sunday():
    f = _parsed_fixtures()
    # 2026-03-08 is a Sunday with cti 60; a 03-09 row (via stress) gets 7×60 .. ×1.8
    payload = mcp.build_scrape_payload(date(2026, 3, 9), "America/Mexico_City", [], {}, f["sleep_hrv"],
                                       f["resting_hr"], f["load"], stress={date(2026, 3, 9): 22})
    rows = {d["happenDay"]: d for d in payload["evolab"]["analyse_query"]["dayList"]}
    assert rows[20260309]["recomendTlMin"] == 420.0 and rows[20260309]["recomendTlMax"] == 756.0
    assert "recomendTlMin" not in rows[20260308]  # its previous Sunday (03-01) isn't in the window
    assert payload["today_status"] == "no_today_row" and payload["missing"] == []


def test_evening_session_stays_on_its_local_day():
    start = datetime(2026, 3, 8, 20, 30, tzinfo=ZoneInfo("America/Mexico_City"))
    rec = {"sport": "Indoor Run", "date": date(2026, 3, 8), "name": "late run", "start": int(start.timestamp()),
           "end": int(start.timestamp()) + 1800, "duration_s": 1800, "distance_km": 4.0, "pace_s": 450,
           "avg_hr": 150, "calories": 300, "sets": None, "labelId": "1", "sportType": 101}
    payload = mcp.build_scrape_payload(date(2026, 3, 8), "America/Mexico_City", [rec], {}, {}, {}, {})
    assert payload["activities"][0]["startTimeLocal"] == "2026-03-08T20:30:00"
    # the trap the council caught: naive UTC would have filed it under the next day
    assert datetime.utcfromtimestamp(rec["start"]).date() == date(2026, 3, 9)


def test_partial_today_is_flagged_for_fallback():
    f = _parsed_fixtures()
    load = mcp.parse_training_load(fixture("coros_mcp_training_load_reworded.txt"))  # ati/cti None
    payload = mcp.build_scrape_payload(date(2026, 3, 8), "America/Mexico_City", [], {}, f["sleep_hrv"],
                                       f["resting_hr"], load)
    assert payload["today_status"] == "partial"
    assert set(payload["missing"]) == {"ati", "cti", "tib", "fatigue_state", "load_ratio"}
    day = next(d for d in payload["evolab"]["analyse_query"]["dayList"] if d["happenDay"] == 20260308)
    assert "ati" not in day and day["avgSleepHrv"] == 52
