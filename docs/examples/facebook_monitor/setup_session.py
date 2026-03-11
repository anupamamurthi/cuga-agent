"""
Run this script ONCE to log into Facebook and save your browser session.
After that, the monitor will reuse the saved session automatically.

Usage:
    python setup_session.py
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = "./fb_session.json"


async def main():
    print("Opening Facebook login page...")
    print("Please log in manually. The session will be saved when you close the browser.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.facebook.com/login")

        print("Waiting for you to log in... (browser will auto-close after login is detected)")
        print("If you have 2FA, complete it too.\n")

        # Wait until we're redirected away from the login page (login successful)
        try:
            await page.wait_for_url(
                lambda url: "facebook.com" in url and "/login" not in url,
                timeout=120_000,
            )
            await page.wait_for_timeout(2000)  # small delay to let cookies settle
        except Exception:
            print("Timed out waiting for login. Please try again.")
            await browser.close()
            return

        # Save session state (cookies + local storage)
        storage = await context.storage_state()
        Path(SESSION_FILE).write_text(json.dumps(storage, indent=2))
        print(f"\nSession saved to {SESSION_FILE}")
        print("You can now run: python monitor.py")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
