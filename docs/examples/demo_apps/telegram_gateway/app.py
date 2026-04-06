"""
Telegram Gateway — one agent, two front-ends.
=============================================

The same CugaAgent responds on both a browser chat UI and a Telegram bot
simultaneously. Every user gets their own conversation thread.

Architecture:

  Browser  →  ConversationGateway  →  CugaAgent
  Telegram →  ConversationGateway  →  (same agent, separate thread per chat_id)

Supported inbound (Telegram):
  text       → routed directly
  voice/m4a  → transcribed via Whisper/RITS, then routed
  photo      → saved to temp file, agent gets the path
  document   → text/PDF extracted, prepended to message

Run:
    python app.py
    python app.py --provider rits
    python app.py --no-browser          # Telegram only
    python app.py --port 8775           # custom browser port

    Then open http://127.0.0.1:8775
    AND message your Telegram bot directly.

Required:
    TELEGRAM_BOT_TOKEN   from @BotFather
    One of: RITS_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY

Optional tools (uncomment in make_agent below):
    TAVILY_API_KEY       for web search
"""
from __future__ import annotations

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
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    tools = []

    # Uncomment to give the agent web search:
    # try:
    #     from cuga_channels import make_web_search_tool
    #     tools.append(make_web_search_tool())
    # except Exception:
    #     pass

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=tools,
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
    )


async def main(host: str, port: int, browser: bool) -> None:
    from cuga_channels import ConversationGateway

    agent   = make_agent()
    gateway = ConversationGateway(agent=agent)

    if browser:
        gateway.add_browser_adapter(host=host, port=port, title="Personal Assistant")

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        gateway.add_telegram_adapter(token=telegram_token)
    else:
        if not browser:
            print("\n  ⚠️  TELEGRAM_BOT_TOKEN not set and --no-browser was passed.")
            print("  Nothing to start. Set TELEGRAM_BOT_TOKEN or remove --no-browser.\n")
            return
        print("\n  ℹ️  TELEGRAM_BOT_TOKEN not set — running browser only.")
        print("  Set TELEGRAM_BOT_TOKEN to also enable the Telegram bot.\n")

    await gateway.start()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Telegram Gateway — one agent, browser + Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py --provider rits
              python app.py --provider anthropic --port 8775
              python app.py --no-browser          # Telegram only

            Then open http://127.0.0.1:8775
            AND message your Telegram bot.

            Both front-ends talk to the same agent.
            Each Telegram user gets their own conversation thread.
        """),
    )
    parser.add_argument("--host",       default="127.0.0.1")
    parser.add_argument("--port",       type=int, default=8775)
    parser.add_argument("--provider",   "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",      "-m", default=None)
    parser.add_argument("--no-browser", action="store_true",
        help="Disable the browser UI — run Telegram adapter only")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if not args.no_browser:
        print(f"\n  Personal Assistant  →  http://{args.host}:{args.port}")
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        print(f"  Telegram bot       →  message @your_bot on Telegram")
    print()

    asyncio.run(main(args.host, args.port, browser=not args.no_browser))
