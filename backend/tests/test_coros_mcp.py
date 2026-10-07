"""COROS MCP shadow client — parsers, derivations, guards, report.

The MCP answers in prose, so the parser is the only piece that can be wrong
silently. Fixtures mirror the exact templates sampled 2026-10-07 (values are
invented). The council's rules under test: a missing or reworded line yields
None (never 0), write tools are refused, and the shadow path never reaches
COROS unless explicitly enabled.
"""
import os
from datetime import date
from types import SimpleNamespace

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

def test_shadow_disabled_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("COROS_MCP_TOKEN_ROOT", str(tmp_path))
    monkeypatch.delenv("COROS_MCP_TOKEN_PATH", raising=False)
    monkeypatch.delenv("COROS_MCP_SHADOW", raising=False)
    assert mcp.find_token_path() is None
    assert mcp.shadow_enabled() is False
    with pytest.raises(mcp.CorosMcpError, match="no COROS MCP token"):
        mcp.ensure_token()


def test_shadow_enabled_with_token_and_off_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("COROS_MCP_TOKEN_ROOT", str(tmp_path))
    monkeypatch.delenv("COROS_MCP_TOKEN_PATH", raising=False)
    monkeypatch.delenv("COROS_MCP_SHADOW", raising=False)
    path = mcp.save_token({"issuer": "https://mcpus.coros.com", "client_id": "c",
                           "access_token": "a", "refresh_token": "r",
                           "expires_at": 9_999_999_999, "obtained_at": 0})
    assert path == tmp_path / "us" / "token.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert mcp.shadow_enabled() is True
    assert mcp.ensure_token()["access_token"] == "a"  # fresh → no refresh call
    monkeypatch.setenv("COROS_MCP_SHADOW", "0")
    assert mcp.shadow_enabled() is False


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
