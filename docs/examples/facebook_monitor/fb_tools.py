"""
LangChain tools given to the CUGA sub-agent:
  - fetch_group_posts   — scrape recent posts from a Facebook group
  - send_email_alert    — send an email notification via SMTP
  - send_sms_alert      — send an SMS via Twilio (optional)
"""

import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from pathlib import Path

from langchain_core.tools import tool
from loguru import logger


# ---------------------------------------------------------------------------
# Shared config (injected at runtime by monitor.py)
# ---------------------------------------------------------------------------
_config: dict = {}


def set_config(config: dict) -> None:
    global _config
    _config = config


# ---------------------------------------------------------------------------
# Facebook scraping tool
# ---------------------------------------------------------------------------

async def _scrape_group_async(group_url: str, session_file: str, limit: int) -> list[dict]:
    """Async Playwright scraper — must be awaited from within a running event loop."""
    from playwright.async_api import async_playwright

    if not Path(session_file).exists():
        raise FileNotFoundError(
            f"Session file not found: {session_file}\n"
            "Run setup_session.py first to log in and save your Facebook session."
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=session_file)
        page = await context.new_page()

        try:
            await page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)  # let dynamic content render

            # Scroll a bit to load more posts
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(1500)

            # Extract post text from the feed
            # Facebook's DOM is dynamic; we look for the main feed article elements
            posts = await page.evaluate("""() => {
                const results = [];
                // Posts are wrapped in role="article" divs in the group feed
                const articles = document.querySelectorAll('[role="article"]');
                for (const article of articles) {
                    const text = article.innerText?.trim();
                    if (text && text.length > 20) {
                        results.push(text.substring(0, 1000));
                    }
                }
                return results;
            }""")

            return [{"text": post, "url": group_url} for post in posts[:limit]]
        finally:
            await context.close()
            await browser.close()


@tool
def fetch_group_posts(group_url: str, limit: int = 20) -> str:
    """
    Fetch recent posts from a Facebook group using a saved browser session.
    Returns a JSON list of post texts from the group.

    Args:
        group_url: Full URL of the Facebook group (e.g. https://www.facebook.com/groups/123)
        limit: Maximum number of posts to return (default 20)
    """
    session_file = _config.get("facebook_session_file", "./fb_session.json")
    try:
        import asyncio
        posts = asyncio.run(_scrape_group_async(group_url, session_file, limit))
        logger.info(f"Fetched {len(posts)} posts from {group_url}")
        return json.dumps(posts, ensure_ascii=False)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception(f"Error fetching posts from {group_url}")
        return json.dumps({"error": f"Scraping failed: {str(e)}"})


# ---------------------------------------------------------------------------
# Email notification tool
# ---------------------------------------------------------------------------

@tool
def send_email_alert(subject: str, body: str) -> str:
    """
    Send an email notification when a keyword match is found in a Facebook group.

    Args:
        subject: Email subject line
        body: Email body with details about the matching post(s)
    """
    cfg = _config.get("notifications", {}).get("email", {})
    if not cfg.get("enabled", False):
        return "Email notifications are disabled in config."

    smtp_host = cfg["smtp_host"]
    smtp_port = cfg["smtp_port"]
    username = cfg["username"]
    password = cfg["password"]
    to_addr = cfg["to"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(username, password)
            server.sendmail(username, to_addr, msg.as_string())
        logger.info(f"Email alert sent to {to_addr}: {subject}")
        return f"Email sent successfully to {to_addr}"
    except Exception as e:
        logger.exception("Failed to send email")
        return f"Email failed: {str(e)}"


# ---------------------------------------------------------------------------
# SMS notification tool (Twilio)
# ---------------------------------------------------------------------------

@tool
def send_sms_alert(message: str) -> str:
    """
    Send an SMS notification via Twilio when a keyword match is found.

    Args:
        message: The SMS text to send (keep under 160 chars for a single SMS)
    """
    cfg = _config.get("notifications", {}).get("sms", {})
    if not cfg.get("enabled", False):
        return "SMS notifications are disabled in config."

    try:
        from twilio.rest import Client  # type: ignore
    except ImportError:
        return "Twilio not installed. Run: pip install twilio"

    account_sid = cfg["twilio_account_sid"]
    auth_token = cfg["twilio_auth_token"]
    from_number = cfg["from_number"]
    to_number = cfg["to_number"]

    try:
        client = Client(account_sid, auth_token)
        sms = client.messages.create(body=message, from_=from_number, to=to_number)
        logger.info(f"SMS sent: {sms.sid}")
        return f"SMS sent successfully (SID: {sms.sid})"
    except Exception as e:
        logger.exception("Failed to send SMS")
        return f"SMS failed: {str(e)}"


# All tools exposed to the CUGA agent
tools = [fetch_group_posts, send_email_alert, send_sms_alert]
