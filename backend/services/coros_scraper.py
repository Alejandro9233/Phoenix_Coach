import os
import asyncio
import json
from datetime import datetime, timedelta
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
from dotenv import load_dotenv

load_dotenv()

# Extended sport code map
SPORT_CODE_MAP = {
    100: "running",
    101: "running",       # Treadmill
    102: "running",       # Trail running
    104: "running",       # Ultra/trail
    200: "cycling",       # Indoor cycling
    201: "cycling",       # Outdoor cycling
    300: "swimming",      # Pool swimming
    301: "swimming",      # Open water
    402: "strength",      # Strength training
    10000: "triathlon",
}

# Page-number parameter names COROS might use in the list request. Cursor-based
# pagination matches none of these — the backfill then skips instead of guessing.
PAGE_PARAM_KEYS = ("pageNumber", "pageNo", "page")


def _extract_activity_list(json_data):
    """Pull the activity rows out of a captured list payload, or None."""
    if isinstance(json_data, dict):
        if isinstance(json_data.get("list"), list):
            return json_data["list"]
        if isinstance(json_data.get("sportDataList"), list):
            return json_data["sportDataList"]
    return None


def _merge_activities(captured_data, activity_list):
    """Merge rows by labelId — pages accumulate instead of overwriting.

    The sniffer used to be last-one-wins, which was fine for a single page;
    with backfill, page 3 must not erase page 2. Ingestion dedupes by labelId
    anyway, so a re-seen row is harmless.
    """
    by_id = captured_data.setdefault("_activities_by_id", {})
    for item in activity_list:
        if not isinstance(item, dict):
            continue
        key = str(item["labelId"]) if item.get("labelId") else f"anon-{id(item)}"
        by_id[key] = item
    captured_data["activities"] = list(by_id.values())


def _find_page_param(list_request):
    """Locate the page-number parameter in a captured list request.

    Returns ("url"|"body", key), or (None, None) when no recognizable page
    param exists — the caller must skip, never guess.
    """
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(list_request.get("url") or "").query)
    for key in PAGE_PARAM_KEYS:
        if key in query:
            return "url", key
    post_data = list_request.get("post_data")
    if post_data:
        try:
            body = json.loads(post_data)
        except (ValueError, TypeError):
            return None, None
        if isinstance(body, dict):
            for key in PAGE_PARAM_KEYS:
                if key in body:
                    return "body", key
    return None, None


def _build_page_request(list_request, where, key, page_number):
    """Return (url, post_data) requesting `page_number` of the activity list."""
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    url = list_request["url"]
    post_data = list_request.get("post_data")
    if where == "url":
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query[key] = [str(page_number)]
        url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    else:
        body = json.loads(post_data)
        body[key] = page_number
        post_data = json.dumps(body)
    return url, post_data


def _oldest_happen_day(items):
    """Smallest happenDay (int YYYYMMDD) on a page, or None."""
    days = [i.get("happenDay") for i in items
            if isinstance(i, dict) and isinstance(i.get("happenDay"), int)]
    return min(days) if days else None


def _find_access_token(captured_data):
    """The session's API token, sniffed from any captured account payload
    (account/query and friends carry accessToken next to weight/zones)."""
    for payload in (captured_data.get("evolab") or {}).values():
        if isinstance(payload, dict) and payload.get("accessToken"):
            return payload["accessToken"]
    return None


def _normalize_backfill_row(row):
    """Map an activity/query row onto the widget-row schema ingestion reads.

    The sniffed "activity list" is really dashboard/detail/query's recent
    widget; the paged activity/query endpoint names the same facts
    differently (date vs happenDay, startTime vs timestamp, avgHr vs
    avgHeartRate, workoutTime vs duration). Only fields ingestion actually
    consumes are mapped; avgSpeed is deliberately dropped — its unit here is
    not the widget's sec/km, and a guessed pace is worse than none.
    """
    return {
        "labelId": row.get("labelId"),
        "happenDay": row.get("date"),
        "timestamp": row.get("startTime"),
        "duration": row.get("workoutTime") or row.get("totalTime") or 0,
        "distance": row.get("distance") or 0,
        "sportType": row.get("sportType"),
        "avgHeartRate": row.get("avgHr"),
        "avgPower": row.get("avgPower"),
        "totalElevation": row.get("ascent"),
        "trainingLoad": row.get("trainingLoad"),
        "step": row.get("step"),
        "sets": row.get("sets"),
        "pitch": row.get("pitch"),
        "subMode": row.get("subMode"),
    }


class CorosScraper:
    # COROS keeps the `#/login` hash in the URL even after a *successful* login, so the
    # URL can never be used to judge auth state. The reliable signal is DOM-based:
    # the app shell has mounted and the password field is gone. Verified against the
    # live site — the login page has no `.app-container`, and the logged-in dash-board
    # has no `.arco-layout-sider` / `.admin-card-box` (those live on other views).
    LOGGED_IN_JS = """() => !!document.querySelector('.app-container')
                            && !document.querySelector('input[type="password"]')"""

    def __init__(self):
        self.email = os.getenv("COROS_EMAIL")
        self.password = os.getenv("COROS_PASSWORD")
        self.base_url = "https://t.coros.com"

    async def _login_error_text(self, page):
        """Best-effort read of whatever COROS is complaining about on the login form."""
        for selector in ('.arco-form-item-error-help', '.arco-message', '.arco-notice-content'):
            try:
                messages = [t.strip() for t in await page.locator(selector).all_text_contents()]
                messages = [t for t in messages if t]
                if messages:
                    return "; ".join(messages)
            except Exception:
                continue
        return None

    async def _is_logged_in(self, page, timeout=5000):
        """True once the app shell has mounted and the login form is gone."""
        try:
            await page.wait_for_function(self.LOGGED_IN_JS, timeout=timeout)
            return True
        except Exception:
            return False

    async def _check_box(self, page, label):
        """Click an arco checkbox and verify it actually took."""
        checkbox = page.locator(f'label.arco-checkbox:has-text("{label}")')
        for attempt in range(3):
            try:
                await checkbox.click(timeout=5000)
            except Exception as e:
                print(f"  Warning: Could not click '{label}' (attempt {attempt + 1}): {e}")
                continue
            # arco marks the checked state on the label, not the hidden input
            try:
                classes = await checkbox.get_attribute("class", timeout=2000) or ""
                if "arco-checkbox-checked" in classes:
                    print(f"  '{label}' checked.")
                    return True
            except Exception:
                pass
            await page.wait_for_timeout(500)
        print(f"  Warning: '{label}' did not register as checked.")
        return False

    async def login(self, page):
        """Logs into COROS Training Hub."""
        print(f"Logging into COROS as {self.email}...")
        if not self.email or not self.password:
            raise Exception("COROS Login Failed: COROS_EMAIL / COROS_PASSWORD are not set")

        # Same rules as _goto below: domcontentloaded (the `load` event waits
        # on ~500KB of SPA assets) and a tolerated 60s — this is the coldest
        # navigation of the run, and on Render's throttled CPU the 30s
        # Playwright default lost the race three times on 2026-08-27 alone
        # ("Scraper error: Page.goto: Timeout 30000ms"). The form/dashboard
        # waits below are the real readiness judges.
        try:
            await page.goto(f"{self.base_url}/admin/views/dash-board#/login",
                            wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            print("  Login page still loading after 60s — continuing; the "
                  "form wait below is the real verdict.")

        # A remembered session lands us straight on the dashboard, skipping the form.
        if await self._is_logged_in(page):
            print("  Dashboard detected right away. Already logged in!")
            return

        print("  Waiting for login form...")
        # Same lesson as the dashboard wait below: on Render's cold CPU the login
        # SPA can blow well past 15s. The 2026-08-17 morning failure was exactly
        # this — the /login?lastUrl= redirect target renders the same form
        # (verified locally), it just wasn't given time to.
        try:
            await page.wait_for_selector('input[type="text"]', timeout=60000)
        except Exception as e:
            raise Exception(
                f"COROS Login Failed: login form never rendered at {page.url}"
            ) from e

        print("  Login form detected. Filling credentials...")
        await page.fill('input[type="text"]', self.email)
        await page.fill('input[type="password"]', self.password)

        # Verify the consent boxes actually took rather than sleeping a fixed 1s and
        # hoping. Not fatal on its own — the dashboard check below is the real verdict.
        await self._check_box(page, "Remember me")
        await self._check_box(page, "Privacy Policy")

        print("  Clicking Login button...")
        await page.click('button:has-text("Login")')

        print("  Waiting for dashboard to appear...")
        # Render cold starts are slow; 30s was tight enough that a merely-slow login
        # got reported as a failure. Judge on the DOM, never on the URL.
        if await self._is_logged_in(page, timeout=60000):
            print("  Dashboard detected.")
            print("  Login successful.")
            return

        reason = await self._login_error_text(page)
        if reason:
            raise Exception(f"COROS Login Failed: {reason}")
        if await page.locator('input[type="password"]').count():
            raise Exception(
                f"COROS Login Failed: still on the login form after 60s at {page.url} "
                "with no error message — likely rate limiting or rejected credentials"
            )
        raise Exception(
            f"COROS Login Failed: app shell did not mount within 60s at {page.url}"
        )

    # The payloads ingestion actually reads (see IngestionService.ingest_coros_data).
    # Everything else the sniffer catches is a bonus; a scrape that finishes
    # without one of these must say so instead of reporting success.
    def _missing(self, captured_data):
        missing = []
        if not captured_data["activities"]:
            missing.append("activities")
        if "analyse_query" not in captured_data["evolab"]:
            missing.append("recovery metrics (analyse_query)")
        if "dashboard_query" not in captured_data["evolab"]:
            missing.append("HRV baseline (dashboard_query)")
        if not any(k.endswith("_profile") for k in captured_data["evolab"]):
            missing.append("profile")
        return missing

    async def _goto(self, page, url):
        """Fire a navigation so the SPA starts its API calls — that is its only
        job. `domcontentloaded`, not `load`: capture is passive sniffing, so the
        load event is nobody's readiness signal, and waiting for it is how
        2026-08-19 lost a scrape — data-analysis blew the 30s default on
        Render's cold CPU, the raise closed the browser, and payloads that were
        arriving at that moment died with it. A slow navigation is a note, not
        a failure; _missing() at the end is the real verdict."""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            print(f"  Navigation to {url} still loading after 60s — "
                  "continuing on sniffed payloads.")

    async def _settle(self, page, what, predicate, timeout_s=60, grace_ms=2000):
        """Wait until the response sniffer satisfies `predicate`, then a short
        grace for whatever is still in flight. The deadline exists for Render's
        cold CPU; a warm run returns in a couple of seconds. Not fatal on
        timeout — the missing-payload check at the end is the real verdict."""
        waited = 0
        while not predicate():
            if waited >= timeout_s * 1000:
                print(f"  Warning: gave up waiting for {what} after {timeout_s}s.")
                return False
            await page.wait_for_timeout(250)
            waited += 250
        print(f"  {what} captured.")
        await page.wait_for_timeout(grace_ms)
        return True

    # Backfill blast-radius bound: 15 pages ≈ 300 activities ≈ a year of training.
    BACKFILL_PAGE_CAP = 15

    async def _backfill_pages(self, page, captured_data, backfill_days):
        """Fetch older activity-list pages by replaying the captured request.

        Keeps the passive-capture rule: no clicking, no selectors — pages
        2..N are direct fetches of the SAME request this live session just
        made, with its own headers, page number incremented. Stops on: an
        empty page, a page with nothing new, a page older than the cutoff,
        or the page cap.

        Every failure path degrades to "no backfill" (recorded under
        backfill_skipped) — this must never fail the daily recovery-metrics
        scrape it rides on.
        """
        from backend.utils.timezone import get_local_today

        list_request = captured_data.get("_list_request") or {}
        where, key = _find_page_param(list_request) if list_request else (None, None)
        token = _find_access_token(captured_data) if where is None else None
        if where is None and not token:
            print("  Backfill: no pageable list request and no access token — skipping.")
            captured_data["backfill_skipped"] = "no page param and no access token"
            return

        if where is not None:
            # Replay the sniffed request for pages 2.. — page 1 is captured.
            start_page = 2
            # Verbatim except what the mutation invalidates: pseudo-headers
            # and content-length belong to the original exchange.
            replay_headers = {
                k: v for k, v in (list_request.get("headers") or {}).items()
                if not k.startswith(":") and k.lower() not in ("content-length", "host")
            }

            async def fetch_rows(n):
                url, post_data = _build_page_request(list_request, where, key, n)
                resp = await page.request.fetch(
                    url,
                    method=list_request.get("method") or "GET",
                    headers=replay_headers,
                    data=post_data,
                )
                payload = await resp.json()
                rows = _extract_activity_list(
                    payload.get("data", {}) if isinstance(payload, dict) else {})
                return rows or [], None
        else:
            # The sniffed list is dashboard/detail/query's recent widget — it
            # can't page (validated live 2026-08-21). Construct the real list
            # endpoint with the session's own token instead, from page 1: the
            # widget's ~7 rows are not the table's first page.
            start_page = 1

            async def fetch_rows(n):
                url = ("https://teamapi.coros.com/activity/query"
                       f"?size=20&pageNumber={n}&modeList=")
                resp = await page.request.fetch(
                    url, method="GET", headers={"accessToken": token})
                payload = await resp.json()
                d = payload.get("data") if isinstance(payload, dict) else None
                raw = d.get("dataList") if isinstance(d, dict) else None
                rows = [_normalize_backfill_row(r) for r in (raw or [])
                        if isinstance(r, dict) and r.get("labelId") and r.get("startTime")]
                total = d.get("totalPage") if isinstance(d, dict) else None
                return rows, total

        cutoff_day = int(
            (get_local_today() - timedelta(days=backfill_days)).strftime("%Y%m%d"))
        seen = set(captured_data.get("_activities_by_id") or {})
        pages_fetched = 0
        try:
            for page_number in range(start_page, start_page + self.BACKFILL_PAGE_CAP):
                items, total_page = await fetch_rows(page_number)
                if not items:
                    break
                ids = {str(i["labelId"]) for i in items
                       if isinstance(i, dict) and i.get("labelId")}
                if ids and ids <= seen:
                    break  # nothing new — history exhausted
                _merge_activities(captured_data, items)
                seen |= ids
                pages_fetched += 1
                oldest = _oldest_happen_day(items)
                print(f"  Backfill: page {page_number} -> {len(items)} rows (oldest {oldest})")
                if oldest is not None and oldest < cutoff_day:
                    break
                if isinstance(total_page, int) and page_number >= total_page:
                    break
        except Exception as e:
            print(f"  Backfill stopped early (non-fatal): {e}")
            captured_data["backfill_skipped"] = f"error: {e}"
        if pages_fetched:
            captured_data["backfill_pages"] = pages_fetched
            print(f"  Backfill: merged {pages_fetched} extra page(s); "
                  f"{len(captured_data['activities'])} activities total")

    async def scrape_all(self, backfill_days: int = 0):
        """Main entry point: login → EvoLab metrics → Activity list → return.

        `backfill_days > 0` also pages back through the activity list until
        history reaches that many days (or a stop condition in
        _backfill_pages) — used to self-heal a shallow or wiped DB.
        """
        captured_data = {
            "activities": [],
            "evolab": {}
        }
        capture_counts = {"activity_lists": 0}
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1280, 'height': 800})
            page = await context.new_page()
            
            # Intercept network responses to catch the raw JSON data
            async def handle_response(response):
                url = response.url
                content_type = response.headers.get("content-type", "")
                
                if "application/json" in content_type and "teamapi.coros.com" in url:
                    try:
                        data = await response.json()
                        json_data = data.get("data", {})
                        
                        # Catch activity list (any endpoint that returns a list of activities)
                        activity_list = _extract_activity_list(json_data)

                        if activity_list and len(activity_list) > 0:
                            _merge_activities(captured_data, activity_list)
                            # Remember the request so _backfill_pages can replay
                            # it for pages 2..N inside this same session.
                            captured_data["_list_request"] = {
                                "url": url,
                                "method": response.request.method,
                                "headers": await response.request.all_headers(),
                                "post_data": response.request.post_data,
                            }
                            capture_counts["activity_lists"] += 1
                            print(f"    -> SUCCESS: Captured {len(captured_data['activities'])} activities")
                        
                        if any(k in url.lower() for k in ["health", "evolab", "metric", "fitness", "sport", "analyse", "dashboard"]):
                            endpoint = url.split("coros.com/")[-1].split("?")[0].replace("/", "_")
                            captured_data["evolab"][endpoint] = json_data
                            print(f"    -> Captured EvoLab data for: {endpoint}")
                            
                        # Also capture ANY payload that contains weight/profile info (the user's API Code 420BE2BB)
                        elif isinstance(json_data, dict) and any(k in json_data for k in ["weight", "lthrZone", "ftp", "ltspZone"]):
                            endpoint = url.split("coros.com/")[-1].split("?")[0].replace("/", "_") + "_profile"
                            captured_data["evolab"][endpoint] = json_data
                            print(f"    -> Captured Profile data for: {endpoint}")

                    except Exception as e:
                        print(f"    -> Skipped a payload from {url}: {e}")

            page.on("response", handle_response)
            
            try:
                # Navigate by URL and wait on the *sniffed data*, never on tab
                # clicks, DOM selectors, or fixed sleeps. Capture is passive
                # response-sniffing, so a navigation only has to make the page
                # fire its API calls — the payload landing is the one readiness
                # signal that can't lie. The old clock-based waits both wasted
                # ~36s on warm runs and were still too short on Render's cold
                # CPU (2026-08-18: tab clicks outlived their 10s timeouts and
                # analyse_query was lost to a 30s content wait).

                # 1. Login lands on the dashboard, which fires dashboard_query
                # (HRV baseline) and a recent-activity list on its own.
                await self.login(page)
                await self._settle(page, "dashboard_query",
                                   lambda: "dashboard_query" in captured_data["evolab"],
                                   timeout_s=30)

                # 2. Data Analysis page — analyse_query feeds every recovery
                # snapshot; without it the day's HRV/RHR/fatigue never lands.
                print("Navigating to EvoLab metrics...")
                await self._goto(page, f"{self.base_url}/admin/views/data-analysis")
                await self._settle(page, "analyse_query",
                                   lambda: "analyse_query" in captured_data["evolab"])

                # 3. Activity List page — wait for a list response that arrives
                # *after* this navigation (pages merge by labelId in the
                # sniffer). If the dashboard already supplied activities, this
                # is just a top-up, so don't wait long for it.
                print("Navigating to Activity List...")
                lists_before = capture_counts["activity_lists"]
                await self._goto(page, f"{self.base_url}/admin/views/dash-board#/personal/list")
                await self._settle(page, "activity list",
                                   lambda: capture_counts["activity_lists"] > lists_before,
                                   timeout_s=20 if captured_data["activities"] else 60)

                # 3b. Optional history backfill: replay the captured list
                # request for older pages while the session is still alive.
                if backfill_days > 0:
                    await self._backfill_pages(page, captured_data, backfill_days)

                captured_data["missing"] = self._missing(captured_data)
                if captured_data["missing"]:
                    print(f"\n⚠️ Scrape finished, but missing: {', '.join(captured_data['missing'])}")
                else:
                    print(f"\n✅ Scrape complete: {len(captured_data['activities'])} activities, {len(captured_data['evolab'])} EvoLab endpoints")
                return captured_data
            except Exception as e:
                # Render's filesystem is ephemeral, so a screenshot there is write-only
                # debugging. Always log the URL — that survives into the app's error text.
                print(f"Error during scrape at {page.url}: {e}")
                try:
                    shot_dir = os.getenv("SCRAPER_DEBUG_DIR")
                    if shot_dir:
                        shot_path = os.path.join(shot_dir, "scrape_error.png")
                        await page.screenshot(path=shot_path)
                        print(f"Error screenshot saved to {shot_path}")
                except Exception as shot_err:
                    print(f"  (could not save error screenshot: {shot_err})")
                raise e
            finally:
                await browser.close()

if __name__ == "__main__":
    scraper = CorosScraper()
    asyncio.run(scraper.scrape_all())
