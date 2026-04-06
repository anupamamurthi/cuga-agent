"""
Calendar Agent — Google Calendar assistant powered by cuga++
============================================================

Two modes — same agent, same tools:

  🌅 Morning brief  (CronChannel + calendar tools)
     Agent reads today's events and produces a concise daily brief.
     Default: every day at 8am.  Output: Telegram, Email, or log.

  🌐 On-demand query  (WebhookChannel + calendar tools)
     POST {"query": "do I have anything after 3pm?"} → instant calendar answer.
     Also handles scheduling: POST {"query": "schedule team sync tomorrow at 2pm"}

  🔀 Both modes at once  (--both)
     Morning brief on schedule AND on-demand queries via webhook.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • make_calendar_tools() is two lines — get_upcoming_events + create_calendar_event.
  • Same tools work with CronChannel (scheduled) and WebhookChannel (on-demand).
  • Output routing is one import: TelegramChannel, EmailChannel, or LogChannel.
  • Adding GitHub tools is one more line: tools=[*make_calendar_tools(), *make_github_tools()]

─────────────────────────────────────────────────────────────────────────────

Prerequisites (Google Calendar):
    Option A — gcloud (quickest, token expires after 1h):
        gcloud auth application-default login
        export GOOGLE_CALENDAR_ACCESS_TOKEN=$(gcloud auth print-access-token)

    Option B — google-auth library (auto-refresh, recommended):
        pip install google-auth
        gcloud auth application-default login
        (leave GOOGLE_CALENDAR_ACCESS_TOKEN unset — library handles it)

Run:
    python main.py --brief                      # morning brief (logs output)
    python main.py --brief --now                # fire right now for testing
    python main.py --webhook                    # on-demand queries via HTTP
    python main.py --both --now                 # both modes, fires immediately
    python main.py --brief --provider anthropic # use Claude

Webhook mode:
    # Ask about calendar
    curl -X POST http://localhost:18792/calendar \\
         -H "Content-Type: application/json" \\
         -d '{"query": "what do I have on Thursday?"}'

    # Schedule an event
    curl -X POST http://localhost:18792/calendar \\
         -H "Content-Type: application/json" \\
         -d '{"query": "schedule a team standup tomorrow at 10am for 30 minutes"}'

With Telegram output:
    export TELEGRAM_BOT_TOKEN=...
    export TELEGRAM_CHAT_ID=...
    python main.py --brief --now --telegram

Environment variables:
    LLM_PROVIDER                    rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL                       model override
    GOOGLE_CALENDAR_ACCESS_TOKEN    OAuth2 access token (from gcloud)
    GOOGLE_APPLICATION_CREDENTIALS  Path to service account JSON (for google-auth)
    TELEGRAM_BOT_TOKEN              Telegram bot token (for --telegram output)
    TELEGRAM_CHAT_ID                Telegram chat ID (for --telegram output)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_DIR       = Path(__file__).parent
_DEMOS_DIR = _DIR.parent

for _p in [str(_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from cuga_channels import make_calendar_tools
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=make_calendar_tools(),             # get_upcoming_events + create_calendar_event
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


def _make_output_channels(use_telegram: bool):
    from cuga_channels import LogChannel
    channels = [LogChannel()]

    if use_telegram:
        tok = os.getenv("TELEGRAM_BOT_TOKEN")
        cid = os.getenv("TELEGRAM_CHAT_ID")
        if tok and cid:
            from cuga_channels import TelegramChannel
            channels.append(TelegramChannel(token=tok, chat_id=cid))
            log.info("Telegram output enabled → chat %s", cid)
        else:
            log.warning("--telegram set but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing — using log only")

    return channels


# ---------------------------------------------------------------------------
# Mode: brief — scheduled morning calendar digest
# ---------------------------------------------------------------------------

def build_brief_runtime(agent, schedule: str, use_telegram: bool):
    from cuga_channels import CugaRuntime, CronChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=(
                    "Good morning! Produce a concise morning brief.\n\n"
                    "Call get_upcoming_events(days=1) to see today's events, "
                    "then write a short briefing: the day/date, what's scheduled, "
                    "and one motivating sentence to start the day."
                ),
            )
        ],
        output_channels=_make_output_channels(use_telegram),
        require_buffer=False,
        thread_id="calendar-brief",
    )


# ---------------------------------------------------------------------------
# Mode: webhook — on-demand calendar queries and scheduling
# ---------------------------------------------------------------------------

def build_webhook_runtime(agent, port: int, use_telegram: bool):
    from cuga_channels import CugaRuntime, WebhookChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            WebhookChannel(
                port=port,
                path="/calendar",
                message_template=(
                    "A calendar query or scheduling request has arrived.\n\n"
                    "Payload:\n{payload}\n\n"
                    "Extract the 'query' field and handle it:\n"
                    "- If it's a question about events, call get_upcoming_events.\n"
                    "- If it's a scheduling request, call create_calendar_event."
                ),
            )
        ],
        output_channels=_make_output_channels(use_telegram),
        require_buffer=False,
        thread_id="calendar-webhook",
    )


# ---------------------------------------------------------------------------
# Mode: both
# ---------------------------------------------------------------------------

def build_both_runtime(agent, schedule: str, port: int, use_telegram: bool):
    from cuga_channels import CugaRuntime, CronChannel, WebhookChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=(
                    "Good morning! Call get_upcoming_events(days=1) and produce "
                    "a concise morning brief with today's schedule."
                ),
            ),
            WebhookChannel(
                port=port,
                path="/calendar",
                message_template=(
                    "Calendar request:\n{payload}\n\n"
                    "Handle it: read events or create one as needed."
                ),
            ),
        ],
        output_channels=_make_output_channels(use_telegram),
        require_buffer=False,
        thread_id="calendar-both",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(mode: str, schedule: str, port: int, now: bool, use_telegram: bool):
    agent = make_agent()

    if now:
        schedule = "* * * * *"

    if mode == "brief":
        runtime = build_brief_runtime(agent, schedule, use_telegram)
        print(f"\n  Calendar Agent  —  Morning Brief mode")
        print(f"  {'─' * 48}")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: firing every minute for testing)")
        print(f"  {'─' * 48}\n")

    elif mode == "webhook":
        runtime = build_webhook_runtime(agent, port, use_telegram)
        print(f"\n  Calendar Agent  —  Webhook mode")
        print(f"  {'─' * 48}")
        print(f"  Endpoint : http://localhost:{port}/calendar")
        print(f"  {'─' * 48}")
        print(f"\n  Query your calendar:")
        print(f'    curl -X POST http://localhost:{port}/calendar \\')
        print(f'         -H "Content-Type: application/json" \\')
        print(f'         -d \'{{"query": "what do I have tomorrow?"}}\'\n')
        print(f"  Schedule an event:")
        print(f'    curl -X POST http://localhost:{port}/calendar \\')
        print(f'         -H "Content-Type: application/json" \\')
        print(f'         -d \'{{"query": "add team sync tomorrow at 10am for 30 min"}}\'\n')

    else:  # both
        runtime = build_both_runtime(agent, schedule, port, use_telegram)
        print(f"\n  Calendar Agent  —  Brief + Webhook mode")
        print(f"  {'─' * 48}")
        print(f"  Schedule : {schedule}")
        print(f"  Endpoint : http://localhost:{port}/calendar")
        if now:
            print(f"  (--now: brief fires every minute for testing)")
        print(f"  {'─' * 48}\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Calendar Agent — Google Calendar assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--brief",     action="store_true",
                        help="Morning brief mode: daily calendar digest on a schedule")
    parser.add_argument("--webhook",   action="store_true",
                        help="Webhook mode: on-demand calendar queries and scheduling")
    parser.add_argument("--both",      action="store_true",
                        help="Both: morning brief + webhook")
    parser.add_argument("--schedule",  "-s", default="0 8 * * *",
                        help="Cron for brief mode (default: 8am daily)")
    parser.add_argument("--port",      type=int, default=18792,
                        help="Webhook port (default: 18792)")
    parser.add_argument("--now",       action="store_true",
                        help="Fire every minute instead of on schedule (for testing)")
    parser.add_argument("--telegram",  action="store_true",
                        help="Also deliver to Telegram (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    # Credential check
    has_token = bool(
        os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    if not has_token:
        print("\n  NOTE: No Google Calendar credentials detected.")
        print("  The agent will explain how to set them up when first called.")
        print("\n  Quickest setup:")
        print("    gcloud auth application-default login")
        print("    export GOOGLE_CALENDAR_ACCESS_TOKEN=$(gcloud auth print-access-token)\n")

    if args.brief:
        mode = "brief"
    elif args.webhook:
        mode = "webhook"
    elif args.both:
        mode = "both"
    else:
        mode = "webhook"   # default: most interactive for testing

    asyncio.run(run(mode, args.schedule, args.port, args.now, args.telegram))


if __name__ == "__main__":
    main()
