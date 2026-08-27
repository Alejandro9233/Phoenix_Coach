"""Fetch one activity's detail JSON from COROS. Read-only, one activity per call.

The endpoint behind the site's activity page is::

    POST https://teamapi.coros.com/activity/detail/query
         ?screenW=1024&screenH=900&labelId=<id>&sportType=<n>

with an ``accessToken`` header. The screenW/screenH params are mandatory —
without them the API answers ``{"result":"1001","message":"Service
exceptions"}``. The token is sniffed from the account payloads that arrive
during the login the scraper already performs (same trick as
``_find_access_token`` in coros_scraper).

If the direct call fails (COROS changes the contract), we fall back to
loading the real activity page and sniffing the payload the SPA fetches —
the capture path that produced the reference fixture in the first place.
"""

import asyncio
import json

from playwright.async_api import async_playwright

from backend.services.coros_scraper import CorosScraper

DETAIL_URL = ("https://teamapi.coros.com/activity/detail/query"
              "?screenW=1024&screenH=900&labelId={label_id}&sportType={sport_type}")
PAGE_URL = ("https://t.coros.com/activity-detail"
            "?labelId={label_id}&sportType={sport_type}")


class DetailFetchError(Exception):
    pass


async def fetch_activity_detail(label_id, sport_type=100):
    """Login, grab the session token, fetch one activity's detail payload."""
    scraper = CorosScraper()
    state = {"token": None}
    sniffed = []

    async def handle_response(response):
        url = response.url
        if "teamapi.coros.com" not in url:
            return
        try:
            text = await response.text()
        except Exception:
            return
        if not state["token"] and '"accessToken"' in text:
            try:
                data = json.loads(text).get("data")
                if isinstance(data, dict) and data.get("accessToken"):
                    state["token"] = data["accessToken"]
            except (ValueError, TypeError):
                pass
        if str(label_id) in url and '"lapList"' in text:
            sniffed.append(text)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            page.on("response", handle_response)

            await scraper.login(page)
            await scraper._settle(page, "access token",
                                  lambda: state["token"] is not None,
                                  timeout_s=45)
            if not state["token"]:
                raise DetailFetchError("no accessToken seen during login")

            resp = await page.request.fetch(
                DETAIL_URL.format(label_id=label_id, sport_type=sport_type),
                method="POST", headers={"accessToken": state["token"]})
            try:
                payload = await resp.json()
            except Exception:
                payload = None
            # "lapList" merely present (even empty) counts: a lapless-but-valid
            # activity must not trigger the slow sniff, which would only
            # re-fetch the same lapless payload.
            if isinstance(payload, dict) and payload.get("result") == "0000" \
                    and isinstance(payload.get("data"), dict) \
                    and "lapList" in payload["data"]:
                return payload

            # Fallback: let the SPA make the request and sniff it.
            await scraper._goto(
                page, PAGE_URL.format(label_id=label_id, sport_type=sport_type))
            await scraper._settle(page, "detail payload",
                                  lambda: bool(sniffed), timeout_s=90)
            if sniffed:
                try:
                    return json.loads(max(sniffed, key=len))
                except ValueError as e:
                    raise DetailFetchError(
                        f"activity {label_id}: sniffed payload is not valid "
                        f"JSON: {e}") from e
            direct = payload.get("message") if isinstance(payload, dict) \
                else payload
            raise DetailFetchError(
                f"activity {label_id}: direct call returned {direct!r} "
                "and the page sniff saw no lapList payload")
        finally:
            await browser.close()


def fetch_activity_detail_sync(label_id, sport_type=100):
    return asyncio.run(fetch_activity_detail(label_id, sport_type))
