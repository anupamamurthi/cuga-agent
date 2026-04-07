"""
Universal Agent — single chat window for any cuga++ pipeline.

One entry point. The user describes what they want in natural language and the
agent figures out which channels to wire, which tools to give the pipeline agent,
what schedule to use, and where to deliver the output. It then dynamically
registers a factory with the embedded CugaHost and starts the runtime.

Usage
-----
    python app.py
    python app.py --provider anthropic
    python app.py --provider openai --model gpt-4o
    python app.py --port 8770

Then open http://127.0.0.1:8770

Example prompts
---------------
  # RSS pipelines
  "Monitor arxiv for AI papers, email me@co.com every morning"
  "Watch HackerNews for Rust posts, Slack digest every 4 hours"

  # Tool-driven pipelines (no data channel, agent fetches its own data)
  "Check Bitcoin and Ethereum prices every hour and log them"
  "Send me a daily GitHub summary of open PRs in my repo"
  "Every morning at 8, email me my calendar events for the day"
  "Search for AI news weekly and send a digest to Slack"

  # Document / audio pipelines
  "Watch ~/Downloads for PDFs and summarise them, log output"
  "Transcribe audio files in ./recordings every 30 minutes"

  # Messaging pipelines
  "Monitor my Gmail inbox every 10 minutes, summarise new emails to Slack"

  # Management
  "list my pipelines"
  "stop the arxiv pipeline"
  "update the arxiv pipeline to run at 9am instead"

Architecture
------------
  Browser chat  →  ConversationGateway
                   ↓
                   CugaAgent (planner)
                   ├── create_pipeline  → host.register_factory() + client.start_runtime()
                   ├── update_pipeline  → client.update_runtime()
                   ├── stop_pipeline    → client.stop_runtime()
                   └── list_pipelines   → client.list_runtimes()

  CugaHost (embedded)
  └── dynamic factories (one per pipeline, registered by create_pipeline)
      └── CugaRuntime
          ├── DataChannel (rss | imap | slack | docling | audio | ...)
          ├── CronChannel (schedule from spec)
          ├── CugaAgent   (pipeline agent with task-specific tools)
          └── OutputChannel (email | slack | telegram | discord | sms | log)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_APP_DIR   = Path(__file__).parent
_DEMOS_DIR = _APP_DIR.parent

for _p in [str(_APP_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


def make_planner_agent(host, client):
    """Build the universal planner CugaAgent with the four pipeline management tools."""
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm
    from planner_tools import make_planner_tools

    tools = make_planner_tools(host=host, client=client)

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=tools,
        plugins=[CugaSkillsPlugin(skills_dir=str(_APP_DIR / "skills"))],
        cuga_folder=str(_APP_DIR / ".cuga"),
    )


async def main(host: str, port: int) -> None:
    from cuga_channels import ConversationGateway, CugaHostClient

    # Embed CugaHost in-process so we can register dynamic factories.
    # If a host is already running on 18790, connect_or_embed() returns
    # (None, client) and we skip in-process hosting.
    cuga_host, client = await CugaHostClient.connect_or_embed(
        state_dir=_APP_DIR / ".cuga" / "host",
    )

    agent   = make_planner_agent(host=cuga_host, client=client)
    gateway = ConversationGateway(agent=agent)
    gateway.add_browser_adapter(host=host, port=port, title="Universal Agent")

    print(f"\n  Universal Agent  →  http://{host}:{port}\n")
    print("  Try:\n"
          '    "Monitor arxiv for AI papers, email me@co.com every morning"\n'
          '    "Check Bitcoin price every hour and log it"\n'
          '    "Send me my Google Calendar events daily at 8am"\n'
          '    "list my pipelines"\n'
          '    "stop all"\n')

    try:
        await gateway.start()
    finally:
        if cuga_host is not None:
            await cuga_host.stop()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Universal Agent — single chat UI for any cuga++ pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider openai --model gpt-4o
              python app.py --port 8770

            Then open http://127.0.0.1:8770

            Prompts to try (organised by channel type):

            RSS pipelines:
              "Monitor arxiv for AI agent research, email me@co.com every morning"
              "Watch HN for Rust posts, Slack digest to #tech every 4 hours"
              "arxiv + HuggingFace AI news, weekly digest, email me@co.com"

            Tool-driven pipelines (agent fetches its own data):
              "Check Bitcoin and Ethereum prices every hour, log the result"
              "Daily stock summary for AAPL, MSFT, NVDA — email me@co.com at 9am"
              "Every morning at 8am email me my calendar events for the day"
              "Search for LangChain release notes every Monday, log a summary"
              "Weekly GitHub PR digest for anthropics/claude-code, email me@co.com"

            Document pipelines:
              "Watch ~/Downloads for new PDFs and summarise them every 10 minutes"
              "Process documents in ./inbox, email summaries to me@co.com daily"

            Audio pipelines:
              "Transcribe voice memos in ./recordings every 30 min, log the text"

            Management:
              "list my pipelines"
              "stop the arxiv pipeline"
              "update the bitcoin pipeline to run every 30 minutes"
              "stop all"
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8770)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    asyncio.run(main(args.host, args.port))
