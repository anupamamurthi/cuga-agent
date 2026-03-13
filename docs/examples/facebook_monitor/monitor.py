"""
Facebook Group Monitor — CUGA Sub-Agent
=======================================
Periodically asks CUGA to check Facebook groups for keywords and send
notifications. No changes to CUGA core — this is a standalone sub-agent
that uses the CugaAgent SDK and injects custom LangChain tools.

Usage:
    1. Run setup_session.py once to log in and save your Facebook session
    2. Edit config.yaml with your groups, keywords, and notification settings
    3. Run: python monitor.py
"""

import os
import asyncio
import yaml
from datetime import datetime
from pathlib import Path

from loguru import logger

# Point to the root .env so CUGA picks up your LLM credentials
os.environ["ENV_FILE"] = str(Path(__file__).parents[3] / ".env")
os.environ["DYNA_CONF_ADVANCED_FEATURES__MODE"] = "api"
os.environ["DYNA_CONF_FEATURES__LOCAL_SANDBOX"] = "true"

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.controller import AgentRunner as CugaAgent

import fb_tools
from fb_tools import tools as fb_tool_list


# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

def load_config(path: str = "./config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Initialize CUGA agent (done once, reused across checks)
# ---------------------------------------------------------------------------

_cuga_agent: CugaAgent | None = None
_tracker = ActivityTracker()


async def get_agent() -> CugaAgent:
    global _cuga_agent
    if _cuga_agent is None:
        logger.info("Initializing CUGA agent...")
        _cuga_agent = CugaAgent(browser_enabled=False)
        await _cuga_agent.initialize_appworld_env()
        for tool in fb_tool_list:
            tool.metadata = {"server_name": "facebook_monitor"}
        _tracker.set_tools(fb_tool_list)
        logger.info("CUGA agent ready.")
    return _cuga_agent


# ---------------------------------------------------------------------------
# Build the task prompt for CUGA
# ---------------------------------------------------------------------------

def build_task(config: dict) -> str:
    groups = config["groups"]
    keywords = config["keywords"]
    interval = config["check_interval_minutes"]
    posts_per_group = config.get("posts_per_group", 20)

    email_enabled = config["notifications"]["email"].get("enabled", False)
    sms_enabled = config["notifications"]["sms"].get("enabled", False)

    notify_via = []
    if email_enabled:
        notify_via.append("email (use send_email_alert)")
    if sms_enabled:
        notify_via.append("SMS (use send_sms_alert)")

    if not notify_via:
        notify_via = ["email (use send_email_alert)"]

    group_list = "\n".join(
        f"  - {g['name']}: {g['url']}" for g in groups
    )
    keyword_list = ", ".join(f'"{k}"' for k in keywords)
    notification_instructions = " and ".join(notify_via)

    return f"""
You are a Facebook group monitor. Your job is to check the following groups for keyword matches and notify the user.

GROUPS TO CHECK (fetch {posts_per_group} posts from each using fetch_group_posts):
{group_list}

KEYWORDS TO WATCH FOR (case-insensitive): {keyword_list}

INSTRUCTIONS:
1. For each group, call fetch_group_posts with its URL and limit={posts_per_group}
2. Scan the returned posts for any of the keywords
3. For each matching post, collect: the keyword found, a short excerpt of the post, and the group name
4. If ANY matches were found across all groups, send ONE consolidated notification via {notification_instructions}
   - Subject/message should say: "Facebook keyword alert — [matched keywords] found in [group names]"
   - Body should list each match with its excerpt
5. If NO matches were found, just report that no matches were found — do NOT send any notification
6. Return a summary of what you found and what you did

Current check time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
""".strip()


# ---------------------------------------------------------------------------
# Single monitor cycle
# ---------------------------------------------------------------------------

async def run_check(config: dict) -> None:
    logger.info("Starting Facebook group check...")
    agent = await get_agent()
    task = build_task(config)

    try:
        result = await agent.run_task_generic(eval_mode=False, goal=task)
        logger.info(f"Check complete. Agent response:\n{result.answer}")
    except Exception:
        logger.exception("Error during Facebook group check")


# ---------------------------------------------------------------------------
# Main scheduler loop
# ---------------------------------------------------------------------------

async def main():
    config = load_config()
    fb_tools.set_config(config)

    interval_minutes = config.get("check_interval_minutes", 30)
    interval_seconds = interval_minutes * 60

    logger.info(
        f"Facebook Monitor started. "
        f"Checking {len(config['groups'])} group(s) every {interval_minutes} minutes."
    )
    logger.info(f"Keywords: {config['keywords']}")

    # Run immediately on start, then on interval
    while True:
        await run_check(config)
        logger.info(f"Next check in {interval_minutes} minutes...")
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
