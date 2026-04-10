"""
Discord Agent — bidirectional Discord bot powered by cuga++
============================================================

Two modes — same agent, same skills:

  💬 Chat mode  (DiscordDataChannel + DiscordChannel)
     Your bot reads a Discord channel and responds to every new message.
     Drop a question in Discord → get an AI answer back.

  📅 Digest mode  (CronChannel + DiscordChannel via webhook)
     Scheduled summaries or announcements posted to Discord.
     Default: weekly digest every Monday at 9am.

  🔀 Both modes at once  (--both)
     Agent responds to messages AND posts the weekly digest.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • DiscordDataChannel (bot token) → reads messages from a channel.
  • DiscordChannel supports two delivery modes: webhook OR bot token.
  • Switching between Telegram/Discord/Slack is one line — same agent code.
  • Webhook output is the simplest Discord setup: no bot, no permissions.

─────────────────────────────────────────────────────────────────────────────

Prerequisites — Webhook mode (output-only, simplest):
    1. In Discord: right-click channel → Edit → Integrations → Webhooks
    2. New Webhook → copy URL
    3. export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

Prerequisites — Bot token mode (bidirectional chat):
    1. https://discord.com/developers/applications → New Application → Bot
    2. Copy bot token → export DISCORD_BOT_TOKEN=your.token.here
    3. OAuth2 → URL Generator → scopes: bot
       Permissions: Send Messages, Read Message History, View Channels
    4. Open invite URL → add bot to your server
    5. Right-click channel → Copy Channel ID (enable Developer Mode first)
    6. export DISCORD_CHANNEL_ID=123456789012345678

Run:
    python main.py --chat                          # respond to messages (needs bot token)
    python main.py --digest                        # weekly digest (webhook or bot token)
    python main.py --both                          # chat + digest
    python main.py --digest --now                  # fire digest right now (for testing)
    python main.py --both --provider anthropic

Environment variables:
    LLM_PROVIDER          rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL             model override
    DISCORD_WEBHOOK_URL   Webhook URL (for digest/output-only mode)
    DISCORD_BOT_TOKEN     Bot token (for chat mode or bot-token output)
    DISCORD_CHANNEL_ID    Channel ID to read from (chat mode)
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
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=[],
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


def _make_output_channel():
    """Return DiscordChannel — prefer webhook URL, fall back to bot token."""
    from cuga_channels import DiscordChannel
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if webhook_url:
        return DiscordChannel(webhook_url=webhook_url, username="cuga agent")
    return DiscordChannel(
        token=os.getenv("DISCORD_BOT_TOKEN"),
        channel_id=os.getenv("DISCORD_CHANNEL_ID"),
    )


# ---------------------------------------------------------------------------
# Mode: chat — respond to Discord messages
# ---------------------------------------------------------------------------

def build_chat_runtime(agent):
    from cuga_channels import CugaRuntime, DiscordDataChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            DiscordDataChannel(
                token=os.getenv("DISCORD_BOT_TOKEN"),
                channel_id=os.getenv("DISCORD_CHANNEL_ID"),
                poll_seconds=10,
            ),
        ],
        output_channels=[_make_output_channel()],
        require_buffer=True,
        thread_id="discord-chat",
    )


# ---------------------------------------------------------------------------
# Mode: digest — scheduled Discord posts
# ---------------------------------------------------------------------------

def build_digest_runtime(agent, schedule: str, message: str):
    from cuga_channels import CugaRuntime, CronChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[CronChannel(schedule=schedule, message=message)],
        output_channels=[_make_output_channel()],
        require_buffer=False,
        thread_id="discord-digest",
    )


# ---------------------------------------------------------------------------
# Mode: both
# ---------------------------------------------------------------------------

def build_both_runtime(agent, schedule: str, message: str):
    from cuga_channels import CugaRuntime, DiscordDataChannel, CronChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            DiscordDataChannel(
                token=os.getenv("DISCORD_BOT_TOKEN"),
                channel_id=os.getenv("DISCORD_CHANNEL_ID"),
                poll_seconds=10,
            ),
            CronChannel(schedule=schedule, message=message),
        ],
        output_channels=[_make_output_channel()],
        require_buffer=False,
        thread_id="discord-both",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(mode: str, schedule: str, now: bool):
    agent = make_agent()

    digest_message = (
        "Produce a short weekly team digest for a Discord server.\n\n"
        "Include: (1) a motivating thought to kick off the week, "
        "(2) a reminder of what good teamwork looks like, "
        "(3) one interesting tech fact or trend from the past week."
    )

    if mode == "chat":
        runtime = build_chat_runtime(agent)
        print(f"\n  Discord Agent  —  Chat mode")
        print(f"  {'─' * 44}")
        print(f"  Post a message in your Discord channel → bot responds.")
        print(f"  Polling every 10 seconds.")
        print(f"  {'─' * 44}\n")

    elif mode == "digest":
        if now:
            schedule = "* * * * *"
        runtime = build_digest_runtime(agent, schedule, digest_message)
        print(f"\n  Discord Agent  —  Digest mode")
        print(f"  {'─' * 44}")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: firing every minute for testing)")
        print(f"  {'─' * 44}\n")

    else:  # both
        if now:
            schedule = "* * * * *"
        runtime = build_both_runtime(agent, schedule, digest_message)
        print(f"\n  Discord Agent  —  Chat + Digest mode")
        print(f"  {'─' * 44}")
        print(f"  Chat     : responds to Discord messages")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: digest fires every minute for testing)")
        print(f"  {'─' * 44}\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Discord Agent — bidirectional Discord bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider", "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--chat",      action="store_true",
                        help="Chat mode: respond to Discord messages (needs bot token)")
    parser.add_argument("--digest",    action="store_true",
                        help="Digest mode: scheduled posts (webhook URL is enough)")
    parser.add_argument("--both",      action="store_true",
                        help="Both: chat + digest (needs bot token)")
    parser.add_argument("--schedule",  "-s", default="0 9 * * 1",
                        help="Cron for digest mode (default: Monday 9am)")
    parser.add_argument("--now",       action="store_true",
                        help="Fire every minute instead of on schedule (for testing)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    # Validate config
    webhook  = os.getenv("DISCORD_WEBHOOK_URL")
    token    = os.getenv("DISCORD_BOT_TOKEN")
    chan_id  = os.getenv("DISCORD_CHANNEL_ID")

    if not webhook and not token:
        print("\n  ERROR: No Discord credentials set.")
        print("  For output-only (digest mode):")
        print("    export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...")
        print("  For bidirectional (chat mode):")
        print("    export DISCORD_BOT_TOKEN=...")
        print("    export DISCORD_CHANNEL_ID=...\n")
        return

    if (args.chat or args.both) and (not token or not chan_id):
        print("\n  ERROR: Chat mode needs DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID.")
        print("  export DISCORD_BOT_TOKEN=...")
        print("  export DISCORD_CHANNEL_ID=...\n")
        return

    if args.chat:
        mode = "chat"
    elif args.digest:
        mode = "digest"
    elif args.both:
        mode = "both"
    else:
        mode = "digest"   # default: simplest (webhook output, no bot token needed)

    asyncio.run(run(mode, args.schedule, args.now))


if __name__ == "__main__":
    main()
