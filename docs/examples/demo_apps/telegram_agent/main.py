"""
Telegram Agent — bidirectional Telegram bot powered by cuga++
=============================================================

Two modes — same agent, same skills:

  💬 Chat mode  (TelegramDataChannel + TelegramChannel)
     Your bot responds to every Telegram message.
     Send it a question → get an AI answer back in the same chat.

  📅 Broadcast mode  (CronChannel + TelegramChannel)
     Scheduled messages pushed to your Telegram chat.
     Default: daily morning brief at 8am.

  🔀 Both modes at once  (--both)
     Cron fires the morning brief AND the bot responds to messages.
     This is the most common production pattern.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • TelegramDataChannel + TelegramChannel = full bidirectional bot in ~20 lines.
  • The same agent works in chat mode AND broadcast mode — no code change.
  • Swapping to Discord/Slack/SMS is one line: change the channel class.
  • require_buffer=False — trigger fires even when no data items buffered.

─────────────────────────────────────────────────────────────────────────────

Prerequisites:
    1. Create a bot via @BotFather → /newbot → copy token
    2. Start a chat with your bot (or add to a group)
    3. Get your chat ID:
       curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
       Look for "chat": {"id": <YOUR_CHAT_ID>}
    4. Export env vars:
       export TELEGRAM_BOT_TOKEN=123456789:AAF...
       export TELEGRAM_CHAT_ID=987654321

Run:
    python main.py --chat                          # respond to messages
    python main.py --broadcast                     # morning brief at 8am
    python main.py --both                          # both simultaneously
    python main.py --both --provider anthropic     # use Claude

Test chat mode:
    Send any message to your bot in Telegram — it will respond.

Test broadcast mode immediately:
    python main.py --broadcast --now               # fire once right now

Environment variables:
    LLM_PROVIDER         rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL            model override
    TELEGRAM_BOT_TOKEN   from @BotFather
    TELEGRAM_CHAT_ID     your chat/group ID (for broadcast mode)
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


# ---------------------------------------------------------------------------
# Mode: chat — respond to Telegram messages
# ---------------------------------------------------------------------------

def build_chat_runtime(agent):
    from cuga_channels import (
        CugaRuntime, TelegramDataChannel, TelegramChannel, CronChannel
    )

    return CugaRuntime(
        agent=agent,
        input_channels=[
            TelegramDataChannel(
                token=os.getenv("TELEGRAM_BOT_TOKEN"),
                poll_seconds=3,
            ),
        ],
        output_channels=[
            TelegramChannel(
                token=os.getenv("TELEGRAM_BOT_TOKEN"),
                chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            ),
        ],
        require_buffer=True,   # only fire when someone sends a message
        thread_id="telegram-chat",
    )


# ---------------------------------------------------------------------------
# Mode: broadcast — scheduled Telegram messages
# ---------------------------------------------------------------------------

def build_broadcast_runtime(agent, schedule: str, message: str):
    from cuga_channels import CugaRuntime, CronChannel, TelegramChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(schedule=schedule, message=message),
        ],
        output_channels=[
            TelegramChannel(
                token=os.getenv("TELEGRAM_BOT_TOKEN"),
                chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            ),
        ],
        require_buffer=False,
        thread_id="telegram-broadcast",
    )


# ---------------------------------------------------------------------------
# Mode: both — chat + scheduled broadcast
# ---------------------------------------------------------------------------

def build_both_runtime(agent, schedule: str, message: str):
    from cuga_channels import (
        CugaRuntime, TelegramDataChannel, TelegramChannel, CronChannel
    )

    return CugaRuntime(
        agent=agent,
        input_channels=[
            # DataChannel: buffer incoming messages
            TelegramDataChannel(
                token=os.getenv("TELEGRAM_BOT_TOKEN"),
                poll_seconds=3,
            ),
            # TriggerChannel: also fire on cron schedule
            CronChannel(schedule=schedule, message=message),
        ],
        output_channels=[
            TelegramChannel(
                token=os.getenv("TELEGRAM_BOT_TOKEN"),
                chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            ),
        ],
        require_buffer=False,   # fire on cron even if no messages in buffer
        thread_id="telegram-both",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(mode: str, schedule: str, now: bool):
    agent = make_agent()

    morning_message = (
        "Good morning! Produce a short, upbeat daily brief.\n\n"
        "Include: (1) an inspirational thought for the day, "
        "(2) a reminder to stay focused on what matters, "
        "(3) a random interesting fact."
    )

    if mode == "chat":
        runtime = build_chat_runtime(agent)
        print(f"\n  Telegram Agent  —  Chat mode")
        print(f"  {'─' * 44}")
        print(f"  Send any message to your bot → it will respond.")
        print(f"  {'─' * 44}\n")

    elif mode == "broadcast":
        if now:
            schedule = "* * * * *"   # fire every minute for quick testing
        runtime = build_broadcast_runtime(agent, schedule, morning_message)
        print(f"\n  Telegram Agent  —  Broadcast mode")
        print(f"  {'─' * 44}")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: firing every minute for testing)")
        print(f"  {'─' * 44}\n")

    else:  # both
        if now:
            schedule = "* * * * *"
        runtime = build_both_runtime(agent, schedule, morning_message)
        print(f"\n  Telegram Agent  —  Chat + Broadcast mode")
        print(f"  {'─' * 44}")
        print(f"  Chat     : responds to incoming messages")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: broadcast fires every minute for testing)")
        print(f"  {'─' * 44}\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Telegram Agent — bidirectional Telegram bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider", "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--chat",      action="store_true",
                        help="Chat mode: respond to incoming Telegram messages")
    parser.add_argument("--broadcast", action="store_true",
                        help="Broadcast mode: scheduled messages (default mode)")
    parser.add_argument("--both",      action="store_true",
                        help="Both: chat + scheduled broadcast simultaneously")
    parser.add_argument("--schedule",  "-s", default="0 8 * * *",
                        help="Cron expression for broadcast mode (default: 8am daily)")
    parser.add_argument("--now",       action="store_true",
                        help="For broadcast/both: fire every minute instead of on schedule (for testing)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("\n  ERROR: TELEGRAM_BOT_TOKEN not set.")
        print("  1. Message @BotFather on Telegram → /newbot")
        print("  2. Copy the token and: export TELEGRAM_BOT_TOKEN=...")
        print("  3. Start a chat with your bot.")
        print("  4. Get your chat ID: curl 'https://api.telegram.org/bot<TOKEN>/getUpdates'\n")
        return

    if not os.getenv("TELEGRAM_CHAT_ID") and not args.chat:
        print("\n  WARNING: TELEGRAM_CHAT_ID not set.")
        print("  Broadcast/both modes need a chat ID.")
        print("  Get it: curl 'https://api.telegram.org/bot<TOKEN>/getUpdates'")
        print("  Then: export TELEGRAM_CHAT_ID=<number>\n")

    if args.chat:
        mode = "chat"
    elif args.broadcast:
        mode = "broadcast"
    elif args.both:
        mode = "both"
    else:
        # Default: both (most useful for demo)
        mode = "both"

    asyncio.run(run(mode, args.schedule, args.now))


if __name__ == "__main__":
    main()
