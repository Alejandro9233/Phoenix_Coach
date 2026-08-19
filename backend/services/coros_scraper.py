import os
import asyncio
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

        await page.goto(f"{self.base_url}/admin/views/dash-board#/login")

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

    async def scrape_all(self):
        """Main entry point: login → EvoLab metrics → Activity list → return."""
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
                        activity_list = None
                        if isinstance(json_data, dict):
                            if "list" in json_data and isinstance(json_data["list"], list):
                                activity_list = json_data["list"]
                            elif "sportDataList" in json_data and isinstance(json_data["sportDataList"], list):
                                activity_list = json_data["sportDataList"]
                        
                        if activity_list and len(activity_list) > 0:
                            captured_data["activities"] = activity_list
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
                # *after* this navigation (last one wins in the sniffer). If the
                # dashboard already supplied activities, this is just a top-up,
                # so don't wait long for it.
                print("Navigating to Activity List...")
                lists_before = capture_counts["activity_lists"]
                await self._goto(page, f"{self.base_url}/admin/views/dash-board#/personal/list")
                await self._settle(page, "activity list",
                                   lambda: capture_counts["activity_lists"] > lists_before,
                                   timeout_s=20 if captured_data["activities"] else 60)

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
