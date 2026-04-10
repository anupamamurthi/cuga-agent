"""
Smart Alarm Agent — voice morning briefing powered by cuga++
============================================================

Your agent-powered alarm clock. Every weekday morning it:
  1. Reads your Google Calendar for the day ahead.
  2. Generates a spoken morning briefing (what's on today, any reminders).
  3. Speaks it aloud via TTSChannel (ElevenLabs → macOS say → pyttsx3 → print).
  4. Optionally also sends the briefing to Telegram.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • CronChannel fires at a configured time (default: 7am weekdays).
  • make_calendar_tools() gives the agent access to Google Calendar.
  • TTSChannel speaks the agent's response aloud — no extra code needed.
  • TelegramChannel can deliver the same briefing as a text message.
  • --now flag fires immediately for testing without changing the schedule.

─────────────────────────────────────────────────────────────────────────────

Prerequisites (Google Calendar — pick one):

    Option A — gcloud CLI (quickest, token expires in 1h):
        gcloud auth application-default login
        export GOOGLE_CALENDAR_ACCESS_TOKEN=$(gcloud auth print-access-token)

    Option B — google-auth (auto-refresh, recommended):
        pip install google-auth
        gcloud auth application-default login
        (leave GOOGLE_CALENDAR_ACCESS_TOKEN unset)

TTS backends (auto-selected in priority order):
    1. ElevenLabs (best quality):
        export ELEVENLABS_API_KEY=sk_...
    2. macOS 'say' (no deps, macOS only) — auto-detected
    3. pyttsx3 (cross-platform):
        pip install pyttsx3
    4. Print fallback — always available

Telegram output (optional):
    export TELEGRAM_BOT_TOKEN=123456789:AAF...
    export TELEGRAM_CHAT_ID=987654321

Run:
    python main.py                            # alarm at 7am weekdays, speaks aloud
    python main.py --now                      # fire immediately for testing
    python main.py --time "6:30"              # alarm at 6:30am weekdays
    python main.py --now --telegram           # fire now + send to Telegram
    python main.py --now --voice pyttsx3      # force pyttsx3 backend
    python main.py --now --voice print        # force print (no audio)
    python main.py --provider anthropic --now # use Claude

Schedule examples:
    python main.py --time "7:00"              # 7:00am every weekday (Mon-Fri)
    python main.py --time "8:30"              # 8:30am every weekday
    python main.py --time "7:00" --weekend    # 7:00am every day including weekends

Environment variables:
    LLM_PROVIDER                rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL                   model override
    GOOGLE_CALENDAR_ACCESS_TOKEN  Google OAuth2 token (Option A above)
    ELEVENLABS_API_KEY          ElevenLabs API key (optional, for high-quality TTS)
    TELEGRAM_BOT_TOKEN          Telegram bot token (for --telegram)
    TELEGRAM_CHAT_ID            Telegram chat ID (for --telegram)
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
    from cuga_channels import make_calendar_tools
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=make_calendar_tools(),   # get_upcoming_events + create_calendar_event
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


def _make_output_channels(voice: str, use_telegram: bool):
    """Build the output channel list: TTS always, Telegram optionally."""
    from cuga_channels import TTSChannel

    channels = [TTSChannel(voice=voice)]
    log.info("TTS output enabled (voice='%s')", voice)

    if use_telegram:
        tok = os.getenv("TELEGRAM_BOT_TOKEN")
        cid = os.getenv("TELEGRAM_CHAT_ID")
        if tok and cid:
            from cuga_channels import TelegramChannel
            channels.append(TelegramChannel(token=tok, chat_id=cid))
            log.info("Telegram output also enabled → chat %s", cid)
        else:
            log.warning("--telegram set but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing — TTS only")

    return channels


# ---------------------------------------------------------------------------
# Cron schedule helper
# ---------------------------------------------------------------------------

def _time_to_cron(time_str: str, weekend: bool) -> str:
    """Convert 'HH:MM' to a cron expression for weekdays (or all days)."""
    try:
        hour, minute = time_str.strip().split(":")
        hour   = int(hour)
        minute = int(minute)
    except (ValueError, AttributeError):
        log.warning("Could not parse --time '%s'; defaulting to 7:00", time_str)
        hour, minute = 7, 0

    days = "* " if weekend else "1-5"
    if weekend:
        return f"{minute} {hour} * * *"
    else:
        return f"{minute} {hour} * * 1-5"


# ---------------------------------------------------------------------------
# Runtime builder
# ---------------------------------------------------------------------------

_MORNING_BRIEF_MESSAGE = """
Good morning! Please give me a spoken morning briefing.

Call get_upcoming_events(days=1) to retrieve today's calendar events.

Then deliver a warm, natural-sounding morning briefing that includes:
1. A greeting with the day of the week and date.
2. A summary of today's events (time, title, location if set).
3. If there are no events: let me know the day is clear and wish me a good day.
4. Keep it to 3-5 sentences — this will be spoken aloud, so make it flow naturally
   as if a friendly assistant is speaking.

Do NOT use markdown formatting (no asterisks, headers, or bullets) since this
will be read aloud by TTS. Use plain, conversational language.
""".strip()


def build_runtime(agent, schedule: str, output_channels):
    from cuga_channels import CugaRuntime, CronChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=_MORNING_BRIEF_MESSAGE,
            )
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="smart-alarm",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(time_str: str, weekend: bool, now: bool, voice: str, use_telegram: bool):
    agent    = make_agent()
    outputs  = _make_output_channels(voice, use_telegram)
    schedule = _time_to_cron(time_str, weekend)

    if now:
        schedule = "* * * * *"

    runtime = build_runtime(agent, schedule, outputs)

    print(f"\n  Smart Alarm Agent")
    print(f"  {'─' * 48}")
    if now:
        print(f"  Mode     : fire immediately (--now, every minute)")
    else:
        days = "every day" if weekend else "weekdays (Mon–Fri)"
        print(f"  Alarm    : {time_str}  ({days})")
        print(f"  Schedule : {schedule}")
    print(f"  TTS      : {voice} (auto-selects best backend)")
    if use_telegram:
        print(f"  Telegram : enabled (see logs for status)")
    print(f"  {'─' * 48}")
    print(f"\n  The agent will:")
    print(f"  1. Read your Google Calendar for today")
    print(f"  2. Generate a spoken morning briefing")
    print(f"  3. Speak it aloud via TTSChannel")
    if use_telegram:
        print(f"  4. Also send to Telegram")
    print()

    if not os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN"):
        # Check if google-auth ADC is likely available
        try:
            import google.auth  # type: ignore  # noqa: F401
            log.info("google-auth detected — will use Application Default Credentials")
        except ImportError:
            print("  WARNING: GOOGLE_CALENDAR_ACCESS_TOKEN not set and google-auth not installed.")
            print("  Calendar access may fail. Quick fix:")
            print("    gcloud auth application-default login")
            print("    export GOOGLE_CALENDAR_ACCESS_TOKEN=$(gcloud auth print-access-token)\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Smart Alarm Agent — voice morning briefing from your calendar",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--time",      "-t", default="7:00",
                        help="Alarm time in HH:MM format (default: 7:00)")
    parser.add_argument("--weekend",   action="store_true",
                        help="Also fire on weekends (default: weekdays only)")
    parser.add_argument("--now",       action="store_true",
                        help="Fire every minute instead of on schedule (for testing)")
    parser.add_argument("--voice",     default="auto",
                        choices=["auto", "elevenlabs", "say", "pyttsx3", "print"],
                        help="TTS backend: auto (default), elevenlabs, say, pyttsx3, or print")
    parser.add_argument("--telegram",  action="store_true",
                        help="Also send briefing to Telegram (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    asyncio.run(run(
        time_str     = args.time,
        weekend      = args.weekend,
        now          = args.now,
        voice        = args.voice,
        use_telegram = args.telegram,
    ))


if __name__ == "__main__":
    main()
