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
    def __init__(self):
        self.email = os.getenv("COROS_EMAIL")
        self.password = os.getenv("COROS_PASSWORD")
        self.base_url = "https://t.coros.com"
        
    async def login(self, page):
        """Logs into COROS Training Hub."""
        print(f"Logging into COROS as {self.email}...")
        await page.goto(f"{self.base_url}/admin/views/dash-board#/login")
        
        # Wait for login form
        await page.wait_for_selector('input[type="text"]')
        await page.fill('input[type="text"]', self.email)
        await page.fill('input[type="password"]', self.password)
        
        # Click the checkboxes (Remember me and Privacy Policy)
        print("  Checking 'Remember me'...")
        try:
            await page.click('label.arco-checkbox:has-text("Remember me")', timeout=5000)
        except Exception as e:
            print(f"  Warning: Could not click 'Remember me': {e}")

        print("  Checking 'Privacy Policy'...")
        try:
            await page.click('label.arco-checkbox:has-text("Privacy Policy")', timeout=5000)
        except Exception as e:
            print(f"  Warning: Could not click 'Privacy Policy': {e}")
            
        # Give UI a moment to settle
        await page.wait_for_timeout(1000)

        # Click login button
        print("  Clicking Login button...")
        await page.click('button:has-text("Login")')
        
        # Wait for dashboard to load
        print("  Waiting for dashboard to appear...")
        try:
            # Wait for the main app container or sidebar
            await page.wait_for_selector('.app-container, .arco-layout-sider', timeout=30000)
            
            # Check for login error messages just in case
            error_msg = await page.locator('.arco-form-item-error-help').all_text_contents()
            if error_msg:
                print(f"  ❌ Login Failed. Page says: {error_msg}")
                raise Exception(f"COROS Login Failed: {error_msg}")
                
            print("  Dashboard detected.")
        except Exception as e:
            print(f"  Warning: Dashboard element not detected within 30s. Current URL: {page.url}")
            if "login" in page.url.lower():
                raise e
        
        print("  Login successful.")

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
                        
                        # Catch health/evolab data (analyse, dashboard, etc.)
                        if any(k in url.lower() for k in ["health", "evolab", "metric", "fitness", "sport", "analyse", "dashboard"]):
                            endpoint = url.split("coros.com/")[-1].split("?")[0].replace("/", "_")
                            captured_data["evolab"][endpoint] = json_data
                            print(f"    -> Captured EvoLab data for: {endpoint}")
                            
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
                await page.wait_for_selector('.data-analysis-card-container, .admin-card-box', timeout=30000)
                await page.wait_for_timeout(8000)
                
                # 3. Navigate to Activity List page
                print("Navigating to Activity List...")
                try:
                    print("  Clicking 'Activity List' tab...")
                    await page.click('div.arco-tabs-tab:has-text("Activity List")', timeout=10000)
                except Exception as e:
                    print(f"  Tab click failed, trying direct URL: {e}")
                    await page.goto(f"{self.base_url}/admin/views/dash-board#/personal/list", wait_until="networkidle")
                
                await page.wait_for_selector('.arco-table', timeout=20000)
                await page.wait_for_timeout(5000)
                
                print(f"\n✅ Scrape complete: {len(captured_data['activities'])} activities, {len(captured_data['evolab'])} EvoLab endpoints")
                return captured_data
            except Exception as e:
                print(f"Error during scrape: {e}")
                await page.screenshot(path="scrape_error.png")
                print("Error screenshot saved as scrape_error.png")
                raise e
            finally:
                await browser.close()

if __name__ == "__main__":
    scraper = CorosScraper()
    asyncio.run(scraper.scrape_all())
