"""
Gen AI RSS Watch — runner script
=================================
Monitors generative AI RSS feeds and delivers a curated HTML newsletter
via email whenever something interesting surfaces.

How CUGA fits in
----------------
                                                      ┌─────────────────────────────┐
  RSS Feeds ──► WatchExecutor ──► keyword pre-filter ─►   CugaAgent (editorial AI)  │
  (7 sources)    (polls every      (stage 1: cheap,   │                             │
                  N minutes)        catches genAI      │  • reads matched items      │
                                    + CUGA/ALTK)       │  • judges significance      │
                                                       │  • writes HTML newsletter   │
                                                       │  • calls send_newsletter_   │
                                                       │    email tool               │
                                                       └──────────────┬──────────────┘
                                                                      │
                                                              📧 Email digest

The keyword pre-filter keeps LLM calls cheap: CUGA is only invoked when
there are actual keyword matches, not on every poll cycle.

Usage
-----
    python docs/examples/genai_rss/run.py

    # Override email password via env var instead of editing the JSON:
    WATCH_EMAIL_PASSWORD=yourpassword python docs/examples/genai_rss/run.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from cuga.sdk import CugaAgent
from cuga.watch.executor import WatchExecutor, make_send_email_tool
from cuga.watch.models import WatchConfig


async def main() -> None:
    config_path = Path(__file__).parent / "watch_config_genai_rss.json"
    config = WatchConfig(**json.loads(config_path.read_text()))

    # Build the send_newsletter_email tool from the agent_notify action's SMTP config.
    # This is what lets CUGA actually send email — the tool is injected into the agent
    # so it can call it after composing the digest.
    agent_notify_actions = [a for a in config.actions if a.type == "agent_notify"]
    email_tools = [make_send_email_tool(a) for a in agent_notify_actions if a.smtp_username]

    if not email_tools:
        logger.warning(
            "[genai-rss] No SMTP credentials found in agent_notify actions. "
            "CUGA will log the digest instead of emailing it. "
            "Set smtp_username/smtp_password in the config or WATCH_EMAIL_USERNAME/WATCH_EMAIL_PASSWORD env vars."
        )

    # CugaAgent is the editorial brain:
    # - receives batches of keyword-matched RSS items
    # - uses LLM reasoning to decide what's truly significant
    # - writes a clean HTML newsletter and sends it via send_newsletter_email
    # - stable thread_id means the agent accumulates context across poll cycles
    #   (e.g. it can remember "this is the 3rd mention of CUGA this week")
    agent = CugaAgent(tools=email_tools)

    executor = WatchExecutor(
        config=config,
        cuga_agent=agent,
        thread_id="genai-rss-watch",
    )

    logger.info(
        "[genai-rss] Starting Gen AI RSS Watch\n"
        f"  Feeds:    {[s.name for s in config.sources]}\n"
        f"  Keywords: {config.condition.keywords[:6]} … (+{max(0, len(config.condition.keywords) - 6)} more)\n"
        f"  Action:   CUGA agent_notify → HTML newsletter email\n"
        f"  Archive:  {config.archive_file} (every {config.archive_interval_minutes} min)"
    )

    await executor.run()


if __name__ == "__main__":
    asyncio.run(main())
