import os
import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

async def health_check():
    email = os.getenv("COROS_EMAIL")
    password = os.getenv("COROS_PASSWORD")
    
    if not email or not password or email == "your_email@example.com":
        print("Error: Please set COROS_EMAIL and COROS_PASSWORD in .env")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()
        
        try:
            print(f"Connecting to COROS Training Hub...")
            await page.goto("https://t.coros.com/login")
            
            # Wait for login input
            await page.wait_for_selector('input[type="text"]', timeout=10000)
            await page.fill('input[type="text"]', email)
            await page.fill('input[type="password"]', password)
            
            print("Checking 'Remember me' and 'Privacy Policy'...")
            # Target the labels directly since the inputs are hidden by the UI framework
            try:
                await page.click('label.arco-checkbox:has-text("Remember me")', timeout=5000)
                print("  Clicked 'Remember me'")
            except Exception as e:
                print(f"  Could not click 'Remember me' label: {e}")

            try:
                await page.click('label.arco-checkbox:has-text("Privacy Policy")', timeout=5000)
                print("  Clicked 'Privacy Policy'")
            except Exception as e:
                print(f"  Could not click 'Privacy Policy' label: {e}")

            print("Submitting login...")
            await page.click('button:has-text("Login")')
            
            # Wait for transition
            try:
                # Wait for either the dashboard URL or a specific dashboard element
                print("Waiting for dashboard to load...")
                try:
                    await page.wait_for_selector('.data-analysis-card-container, .admin-card-box', timeout=30000)
                except:
                    # Fallback to URL check
                    await page.wait_for_function('() => !window.location.href.includes("login")', timeout=10000)
                
                final_url = page.url
                print(f"Final URL: {final_url}")
                
                if "login" not in final_url.lower():
                    print("✅ Login Successful! Landed on a non-login page.")
                    
                    # Navigate to the EvoLab metrics page
                    print("Navigating to EvoLab metrics page...")
                    await page.goto("https://t.coros.com/admin/views/data-analysis")
                    
                    # Give it a moment for the boards/metrics to actually load
                    print("Waiting for metrics to finish loading...")
                    await page.wait_for_timeout(10000) # Wait 10 seconds for charts
                    
                    # Take a screenshot of the landing page
                    await page.screenshot(path="coros_evolab_check.png")
                    print("Screenshot saved as coros_evolab_check.png")
                    
                    # Check for "Evolab" tab
                    try:
                        evolab_btn = page.locator('text="EvoLab"')
                        if await evolab_btn.is_visible():
                            print("  Found 'EvoLab' tab!")
                    except: pass
                else:
                    print("❌ Still on login page. Checking for error messages...")
                    error_msg = await page.locator('.arco-form-item-error-help').all_text_contents()
                    if error_msg:
                        print(f"  Page error message: {error_msg}")
                    await page.screenshot(path="coros_login_error.png")
                    print("Screenshot saved as coros_login_error.png")
                    
            except Exception as e:
                print(f"❌ Login failed or timed out. Current URL: {page.url}")
                await page.screenshot(path="coros_login_error.png")
                print(f"Error: {e}")
                
        finally:
            await browser.close()

async def backfill_check(backfill_days):
    """Run the real scraper with pagination against the live site — the only
    way to validate COROS's actual list-request shape before trusting the
    backfill in the daily cron. Prints what a scrape would ingest; writes
    nothing to any database."""
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from backend.services.coros_scraper import CorosScraper

    data = await CorosScraper().scrape_all(backfill_days=backfill_days)
    acts = [a for a in (data.get("activities") or []) if isinstance(a, dict)]
    days = sorted(a["happenDay"] for a in acts if a.get("happenDay"))
    print("\n=== Backfill check ===")
    print(f"Activities captured: {len(acts)}")
    list_request = data.get("_list_request")
    if list_request:
        # URL + body identify the endpoint and its pagination style; headers
        # stay out of the log (they carry the session token).
        print(f"List request: {list_request.get('method')} {list_request.get('url')}")
        print(f"List request body: {str(list_request.get('post_data'))[:300]}")
    if days:
        print(f"Date span: {days[0]} → {days[-1]}")
    if data.get("backfill_pages"):
        print(f"Extra pages fetched: {data['backfill_pages']}")
    if data.get("backfill_skipped"):
        print(f"⚠️ Backfill skipped: {data['backfill_skipped']}")
    if not data.get("backfill_pages") and not data.get("backfill_skipped"):
        print("No backfill ran (first page already reached the cutoff, "
              "or no list request was captured).")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="COROS scraper health check")
    parser.add_argument(
        "--backfill-days", type=int, default=0,
        help="also validate history pagination: run the full scraper and page "
             "back this many days (e.g. 90). 0 = plain login/metrics check.")
    args = parser.parse_args()
    if args.backfill_days > 0:
        asyncio.run(backfill_check(args.backfill_days))
    else:
        asyncio.run(health_check())
