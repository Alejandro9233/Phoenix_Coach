import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
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
        try:
            await page.wait_for_selector('input[type="text"]', timeout=15000)
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

    async def scrape_all(self):
        """Main entry point: login → EvoLab metrics → Activity list → return."""
        captured_data = {
            "activities": [],
            "evolab": {}
        }
        
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
                            
                            if "analyse_query" in endpoint:
                                with open("analyse_debug.json", "w") as f:
                                    import json
                                    json.dump(data, f, indent=2)
                                    
                    except Exception as e:
                        pass

            page.on("response", handle_response)
            
            try:
                # 1. Login
                await self.login(page)
                await page.wait_for_timeout(3000)
                
                # 2. Navigate to Data Analysis (EvoLab) page
                print("Navigating to EvoLab metrics...")
                try:
                    print("  Clicking 'EvoLab Metrics' tab...")
                    await page.click('div.arco-tabs-tab:has-text("EvoLab Metrics")', timeout=10000)
                except Exception as e:
                    print(f"  Tab click failed, trying direct URL: {e}")
                    await page.goto(f"{self.base_url}/admin/views/data-analysis", wait_until="networkidle")
                
                print("  Waiting for EvoLab content...")
                try:
                    await page.wait_for_selector('.data-analysis-card-container, .admin-card-box', timeout=30000)
                except Exception as e:
                    print(f"  Warning: EvoLab content timeout, continuing anyway.")
                await page.wait_for_timeout(8000)
                
                # 3. Navigate to Activity List page
                print("Navigating to Activity List...")
                try:
                    print("  Clicking 'Activity List' tab...")
                    await page.click('div.arco-tabs-tab:has-text("Activity List")', timeout=10000)
                except Exception as e:
                    print(f"  Tab click failed, trying direct URL: {e}")
                    await page.goto(f"{self.base_url}/admin/views/dash-board#/personal/list", wait_until="networkidle")
                
                try:
                    await page.wait_for_selector('.arco-table', timeout=20000)
                except Exception as e:
                    print(f"  Warning: .arco-table timeout, continuing anyway.")
                await page.wait_for_timeout(5000)
                
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
