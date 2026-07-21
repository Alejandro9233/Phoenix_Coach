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

if __name__ == "__main__":
    asyncio.run(health_check())
