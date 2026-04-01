#!/usr/bin/env python3
"""
Newsletter Agent — CugaAgent demo
===================================

An autonomous, event-driven newsletter agent using CugaAgent with:

  cuga-skills    → newsletter_curation.md injected into the system prompt
  cuga-triggers  → CronTrigger (scheduled) + WebhookTrigger (on-demand)
                   passed directly to CugaAgent — no separate TriggerRuntime needed
  CugaAgent      → stateful multi-turn agent with built-in checkpointing

Flow
----
  1. CronTrigger fires on schedule (config.json → "schedule")
     OR WebhookTrigger fires on HTTP POST to /newsletter
  2. CugaAgent dispatches the event via its internal TriggerRuntime
  3. Agent calls fetch_rss() for each source, filtering by keywords
  4. Newsletter curation skill guides: deduplicate → select top N → compose HTML
  5. Agent calls send_email() — delivers via SMTP

Usage
-----
  # Start (scheduled + webhook):
  python docs/examples/demo_apps/newsletter/run.py

  # Override provider:
  python docs/examples/demo_apps/newsletter/run.py --provider rits

  # Fire immediately once (good for testing):
  python docs/examples/demo_apps/newsletter/run.py --run-now

  # Trigger via webhook (while agent is running):
  curl -X POST http://127.0.0.1:8401/newsletter \\
       -H 'Content-Type: application/json' \\
       -d '{"message": "Send the newsletter now", "thread_id": "newsletter-ai-digest"}'

Env vars
--------
  LLM_PROVIDER      rits | watsonx | openai | anthropic | litellm | ollama
  LLM_MODEL         model name override
  SMTP_USERNAME     sender address (required for email delivery)
  SMTP_PASSWORD     SMTP / app password (required)
  NEWSLETTER_TO     comma-separated recipients (required)
  SMTP_HOST         default: smtp.gmail.com
  SMTP_PORT         default: 587
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import textwrap
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("newsletter")

_SKILLS_DIR = _EXAMPLE_DIR / "skills"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _build_trigger_message(cfg: dict) -> str:
    sources    = cfg.get("sources", [])
    source_names = ", ".join(s["name"] for s in sources)
    keywords   = cfg.get("keywords", [])[:8]
    kw_str     = ", ".join(keywords)
    top_n      = cfg.get("top_items_to_select", 12)
    title      = cfg.get("newsletter_title", "AI Digest")

    return textwrap.dedent(f"""\
        It's time to send the {title}.

        Fetch the latest items from these RSS sources: {source_names}.
        Filter for content matching these themes: {kw_str} (and related AI/ML topics).
        Curate the top {top_n} most significant and diverse items, avoiding duplicates \
from previous newsletters.
        Compose a styled HTML newsletter and send it via email.
    """).strip()


# ---------------------------------------------------------------------------
# Build the CugaAgent
# ---------------------------------------------------------------------------

def build_agent(cfg: dict, provider: str | None, model: str | None):
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from cuga_triggers import CronTrigger, WebhookTrigger
    from tools import fetch_rss, send_email

    llm_cfg = cfg.get("llm", {})
    p = provider or os.getenv("LLM_PROVIDER") or llm_cfg.get("provider") or None
    m = model    or llm_cfg.get("model") or None

    from _llm import create_llm
    llm = create_llm(provider=p, model=m)

    thread_id    = cfg.get("thread_id", "newsletter-ai-digest")
    schedule     = cfg.get("schedule", "0 9 * * 1")
    webhook_port = cfg.get("webhook_port", 8401)
    message      = _build_trigger_message(cfg)

    logger.info("Building newsletter CugaAgent: provider=%s model=%s schedule=%s",
                p or "settings-default", m or "(default)", schedule)

    agent = CugaAgent(
        model=llm,
        tools=[fetch_rss, send_email],
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
        triggers=[
            CronTrigger(
                name="newsletter-cron",
                schedule=schedule,
                message=message,
                thread_id=thread_id,
            ),
            WebhookTrigger(
                name="newsletter-webhook",
                path="/newsletter",
                port=webhook_port,
            ),
        ],
    )
    logger.info("Newsletter CugaAgent ready — schedule=%s webhook=:%s/newsletter",
                schedule, webhook_port)
    return agent, message, thread_id, schedule, webhook_port


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

async def run_once(agent, message: str, thread_id: str) -> None:
    logger.info("Running newsletter once — thread_id=%s", thread_id)
    result = await agent.invoke(message, thread_id=thread_id)
    print("\n" + "=" * 60)
    print("Agent response:")
    print("=" * 60)
    print(result.answer)
    print("=" * 60 + "\n")


async def run_daemon(agent, cfg: dict, schedule: str, webhook_port: int) -> None:
    thread_id = cfg.get("thread_id", "newsletter-ai-digest")

    print()
    print("=" * 60)
    print("  Newsletter CugaAgent running")
    print(f"  Schedule   : {schedule}")
    print(f"  Webhook    : POST http://127.0.0.1:{webhook_port}/newsletter")
    print(f"  Thread ID  : {thread_id}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    stop_event = asyncio.Event()

    def _signal_handler(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()
    agent.stop_triggers()
    logger.info("Newsletter agent stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Newsletter agent — scheduled AI digest via email",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python run.py
              python run.py --provider rits
              python run.py --provider watsonx --model meta-llama/llama-4-scout-17b
              python run.py --run-now
              python run.py --config path/to/config.json
        """),
    )
    parser.add_argument("--config", "-c",
        default=str(_EXAMPLE_DIR / "config.json"))
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--run-now", action="store_true",
        help="Fire the newsletter agent once immediately and exit")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))

    agent, message, thread_id, schedule, webhook_port = build_agent(
        cfg, provider=args.provider, model=args.model
    )

    if args.run_now:
        asyncio.run(run_once(agent, message, thread_id))
        agent.stop_triggers()
        return

    asyncio.run(run_daemon(agent, cfg, schedule, webhook_port))


if __name__ == "__main__":
    main()
