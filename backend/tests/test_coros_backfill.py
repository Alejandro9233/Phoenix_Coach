"""D1 history backfill — pure unit tests, no Playwright.

The scraper replays the captured activity-list request for pages 2..N so a
shallow or wiped DB self-heals. Rules locked in here:
- pages merge by labelId (never last-one-wins);
- the page param is found in the query string or JSON body, and its absence
  skips the backfill instead of guessing;
- the loop stops on cutoff, on a page with nothing new, and never raises;
- _backfill_days_needed asks for 90 days only when history is shallow.
"""
import asyncio
import json
from datetime import datetime, time, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import _backfill_days_needed
from backend.models.database import Activity, Base
from backend.services.coros_scraper import (
    CorosScraper,
    _build_page_request,
    _extract_activity_list,
    _find_page_param,
    _merge_activities,
    _oldest_happen_day,
)
from backend.utils.timezone import get_local_today


# ─── list extraction and merging ──────────────────────────────────────────────

def test_extract_handles_both_payload_shapes():
    assert _extract_activity_list({"list": [{"labelId": 1}]}) == [{"labelId": 1}]
    assert _extract_activity_list({"sportDataList": [{"labelId": 2}]}) == [{"labelId": 2}]
    assert _extract_activity_list({"other": 3}) is None
    assert _extract_activity_list(None) is None


def test_merge_by_label_id_dedupes_across_pages():
    captured = {"activities": []}
    _merge_activities(captured, [{"labelId": "a"}, {"labelId": "b"}])
    _merge_activities(captured, [{"labelId": "b", "seen": "again"}, {"labelId": "c"}])
    ids = sorted(a["labelId"] for a in captured["activities"])
    assert ids == ["a", "b", "c"]


# ─── page-param discovery and request mutation ────────────────────────────────

def test_page_param_in_query_string():
    req = {"url": "https://teamapi.coros.com/activity/query?size=20&pageNumber=1",
           "post_data": None}
    assert _find_page_param(req) == ("url", "pageNumber")
    url, post_data = _build_page_request(req, "url", "pageNumber", 3)
    assert "pageNumber=3" in url
    assert post_data is None


def test_page_param_in_json_body():
    req = {"url": "https://teamapi.coros.com/activity/query",
           "post_data": json.dumps({"pageNo": 1, "size": 20})}
    assert _find_page_param(req) == ("body", "pageNo")
    url, post_data = _build_page_request(req, "body", "pageNo", 4)
    assert url == req["url"]
    assert json.loads(post_data)["pageNo"] == 4


def test_cursor_style_request_signals_skip():
    req = {"url": "https://teamapi.coros.com/activity/query?cursor=abc",
           "post_data": json.dumps({"cursor": "abc"})}
    assert _find_page_param(req) == (None, None)


# ─── the backfill loop, against a stubbed page ────────────────────────────────

class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _StubRequestApi:
    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    async def fetch(self, url, method=None, headers=None, data=None):
        self.calls.append(url)
        if len(self.calls) <= len(self._pages):
            return _StubResponse(self._pages[len(self.calls) - 1])
        return _StubResponse({"data": {"list": []}})


class _StubPage:
    def __init__(self, pages):
        self.request = _StubRequestApi(pages)


def _day_int(days_ago):
    return int((get_local_today() - timedelta(days=days_ago)).strftime("%Y%m%d"))


def _captured_with_page1():
    captured = {"activities": [], "evolab": {}}
    _merge_activities(captured, [{"labelId": "p1", "happenDay": _day_int(1)}])
    captured["_list_request"] = {
        "url": "https://teamapi.coros.com/activity/query?size=20&pageNumber=1",
        "method": "POST",
        "headers": {"cookie": "session", ":authority": "drop-me",
                    "content-length": "42"},
        "post_data": None,
    }
    return captured


def test_backfill_merges_until_cutoff():
    pages = [
        {"data": {"list": [{"labelId": "p2", "happenDay": _day_int(30)}]}},
        {"data": {"list": [{"labelId": "p3", "happenDay": _day_int(200)}]}},
        {"data": {"list": [{"labelId": "p4", "happenDay": _day_int(300)}]}},
    ]
    stub = _StubPage(pages)
    captured = _captured_with_page1()

    asyncio.run(CorosScraper()._backfill_pages(stub, captured, backfill_days=90))

    ids = {a["labelId"] for a in captured["activities"]}
    # p3's page crossed the 90-day cutoff: merged, then the loop stopped —
    # p4 was never requested.
    assert ids == {"p1", "p2", "p3"}
    assert len(stub.request.calls) == 2
    assert captured["backfill_pages"] == 2


def test_backfill_stops_when_a_page_brings_nothing_new():
    repeat = {"data": {"list": [{"labelId": "p1", "happenDay": _day_int(1)}]}}
    stub = _StubPage([repeat, repeat])
    captured = _captured_with_page1()

    asyncio.run(CorosScraper()._backfill_pages(stub, captured, backfill_days=90))

    assert len(stub.request.calls) == 1
    assert "backfill_pages" not in captured


def test_backfill_without_page_param_or_token_skips_cleanly():
    captured = _captured_with_page1()
    captured["_list_request"] = {
        "url": "https://teamapi.coros.com/activity/query",
        "method": "GET", "headers": {}, "post_data": None,
    }
    stub = _StubPage([])

    asyncio.run(CorosScraper()._backfill_pages(stub, captured, backfill_days=90))

    assert captured["backfill_skipped"] == "no page param and no access token"
    assert stub.request.calls == []


def test_backfill_fetch_error_is_non_fatal():
    class _BoomRequestApi:
        calls = []

        async def fetch(self, *a, **k):
            raise RuntimeError("network down")

    class _BoomPage:
        request = _BoomRequestApi()

    captured = _captured_with_page1()
    asyncio.run(CorosScraper()._backfill_pages(_BoomPage(), captured, backfill_days=90))

    assert "error" in captured["backfill_skipped"]
    # Page-1 data survives untouched.
    assert {a["labelId"] for a in captured["activities"]} == {"p1"}


def test_oldest_happen_day_ignores_malformed_rows():
    items = [{"happenDay": 20260810}, {"happenDay": "junk"}, {}, "not-a-dict"]
    assert _oldest_happen_day(items) == 20260810
    assert _oldest_happen_day([]) is None


# ─── the trigger condition ────────────────────────────────────────────────────

def _session_with(activity_days_ago=None):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    if activity_days_ago is not None:
        session.add(Activity(
            id="hist-act", athlete_id=1, sport="running",
            start_time=datetime.combine(
                get_local_today() - timedelta(days=activity_days_ago), time(8, 0)),
            duration_sec=3600, distance_m=10000))
        session.commit()
    return session, engine


def test_empty_db_needs_backfill():
    session, engine = _session_with(None)
    try:
        assert _backfill_days_needed(session) == 90
    finally:
        session.close()
        engine.dispose()


def test_shallow_history_needs_backfill():
    session, engine = _session_with(10)
    try:
        assert _backfill_days_needed(session) == 90
    finally:
        session.close()
        engine.dispose()


def test_deep_history_needs_no_backfill():
    session, engine = _session_with(40)
    try:
        assert _backfill_days_needed(session) == 0
    finally:
        session.close()
        engine.dispose()


# ─── the token-constructed activity/query fallback ────────────────────────────
#
# Validated live 2026-08-21: the sniffed "list" is dashboard/detail/query's
# recent widget (unpageable); the real table is activity/query with an
# accessToken header — 612 activities over 31 pages on the real account.

from backend.services.coros_scraper import _find_access_token, _normalize_backfill_row


class _HeaderRecordingApi(_StubRequestApi):
    def __init__(self, pages):
        super().__init__(pages)
        self.headers_seen = []

    async def fetch(self, url, method=None, headers=None, data=None):
        self.headers_seen.append(headers or {})
        return await super().fetch(url, method=method, headers=headers, data=data)


def _activity_query_row(label, days_ago, **extra):
    row = {
        "labelId": label,
        "date": _day_int(days_ago),
        "startTime": 1787000000,
        "workoutTime": 3600,
        "totalTime": 3900,
        "distance": 10000.0,
        "sportType": 100,
        "avgHr": 150,
        "ascent": 80,
        "trainingLoad": 90,
    }
    row.update(extra)
    return row


def test_find_access_token_scans_captured_payloads():
    captured = {"evolab": {
        "analyse_query": {"dayList": []},
        "account_query_profile": {"weight": 76, "accessToken": "tok-123"},
    }}
    assert _find_access_token(captured) == "tok-123"
    assert _find_access_token({"evolab": {}}) is None


def test_normalize_backfill_row_maps_widget_schema():
    out = _normalize_backfill_row(_activity_query_row("x1", 5))
    assert out["labelId"] == "x1"
    assert out["happenDay"] == _day_int(5)
    assert out["timestamp"] == 1787000000
    assert out["duration"] == 3600          # workoutTime preferred
    assert out["avgHeartRate"] == 150       # avgHr renamed
    assert out["totalElevation"] == 80      # ascent renamed
    assert "avgSpeed" not in out            # unit differs from the widget's


def test_token_path_pages_from_one_and_stops_at_cutoff():
    pages = [
        {"data": {"dataList": [_activity_query_row("q1", 5),
                               _activity_query_row("q2", 40)],
                  "totalPage": 31}},
        {"data": {"dataList": [_activity_query_row("q3", 200)],
                  "totalPage": 31}},
        {"data": {"dataList": [_activity_query_row("q4", 300)],
                  "totalPage": 31}},
    ]
    stub_api = _HeaderRecordingApi(pages)

    class _Page:
        request = stub_api

    captured = _captured_with_page1()
    # No page param in the sniffed request; token available from account payload.
    captured["_list_request"] = {"url": "https://teamapi.coros.com/dashboard/detail/query",
                                 "method": "GET", "headers": {}, "post_data": None}
    captured["evolab"] = {"account_query_profile": {"accessToken": "tok-9"}}

    asyncio.run(CorosScraper()._backfill_pages(_Page(), captured, backfill_days=90))

    assert "pageNumber=1" in stub_api.calls[0]
    assert "pageNumber=2" in stub_api.calls[1]
    assert len(stub_api.calls) == 2          # q3's page crossed the cutoff
    assert all(h.get("accessToken") == "tok-9" for h in stub_api.headers_seen)
    ids = {a["labelId"] for a in captured["activities"]}
    assert ids == {"p1", "q1", "q2", "q3"}
    # Normalized rows carry the fields ingestion requires.
    q1 = next(a for a in captured["activities"] if a["labelId"] == "q1")
    assert q1["timestamp"] and q1["duration"] and q1["happenDay"]
    assert captured["backfill_pages"] == 2


def test_token_path_stops_at_total_page():
    pages = [
        {"data": {"dataList": [_activity_query_row("t1", 5)], "totalPage": 1}},
        {"data": {"dataList": [_activity_query_row("t2", 6)], "totalPage": 1}},
    ]
    stub_api = _HeaderRecordingApi(pages)

    class _Page:
        request = stub_api

    captured = _captured_with_page1()
    captured["_list_request"] = {}
    captured["evolab"] = {"account_query_profile": {"accessToken": "tok-9"}}

    asyncio.run(CorosScraper()._backfill_pages(_Page(), captured, backfill_days=90))

    assert len(stub_api.calls) == 1
    assert captured["backfill_pages"] == 1


def test_token_path_drops_rows_missing_required_fields():
    pages = [
        {"data": {"dataList": [
            _activity_query_row("ok1", 5),
            {"labelId": "no-start", "date": _day_int(5)},          # no startTime
            {"startTime": 1787000000, "date": _day_int(5)},        # no labelId
        ], "totalPage": 1}},
    ]
    stub_api = _HeaderRecordingApi(pages)

    class _Page:
        request = stub_api

    captured = _captured_with_page1()
    captured["_list_request"] = {}
    captured["evolab"] = {"account_query_profile": {"accessToken": "tok-9"}}

    asyncio.run(CorosScraper()._backfill_pages(_Page(), captured, backfill_days=90))

    ids = {a["labelId"] for a in captured["activities"]}
    assert ids == {"p1", "ok1"}
