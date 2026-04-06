"""
Web Researcher — Tavily web search + cuga++ pipeline
=====================================================

Demonstrates make_web_search_tool() from cuga-channels.

Two trigger modes — same agent, same tool:

  📅 Scheduled research  (CronChannel)
     Research a topic automatically on a schedule.
     Great for: morning briefs, market monitoring, competitive intel.

  🌐 On-demand research  (WebhookChannel)
     POST {"query": "latest LLM benchmarks"} → instant research report.
     Great for: integrating with Slack bots, n8n workflows, CI pipelines.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • make_web_search_tool() is a one-line import — no boilerplate API wiring.
  • The same tool works in any trigger pattern — cron, webhook, watcher, IMAP.
  • Output routing is one line: LogChannel() or EmailChannel().
  • Adding another tool (calendar, GitHub API) is one more tool in the list.

─────────────────────────────────────────────────────────────────────────────

Prerequisites:
    pip install tavily-python
    export TAVILY_API_KEY=tvly-...

Run:
    python main.py --topic "latest AI agent frameworks" --provider anthropic
    python main.py --webhook --port 18791 --provider rits

Webhook mode:
    curl -X POST http://localhost:18791/research \\
         -H "Content-Type: application/json" \\
         -d '{"query": "open source LLM news this week"}'

Environment variables:
    LLM_PROVIDER    rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL       model override
    TAVILY_API_KEY  Tavily API key (required)
    RESEARCH_TOPIC  default topic for scheduled mode
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
    from cuga_channels import make_web_search_tool
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=[make_web_search_tool()],          # one import, one line
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


# ---------------------------------------------------------------------------
# Mode 1: Scheduled cron research
# ---------------------------------------------------------------------------

def build_cron_runtime(agent, topic: str, schedule: str):
    from cuga_channels import CugaRuntime, CronChannel, LogChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=f"Research this topic and produce a structured report:\n\n{topic}",
            )
        ],
        output_channels=[LogChannel()],
        require_buffer=False,
        thread_id="web-researcher-cron",
    )


# ---------------------------------------------------------------------------
# Mode 2: Webhook on-demand research
# ---------------------------------------------------------------------------

def build_webhook_runtime(agent, port: int):
    from cuga_channels import CugaRuntime, WebhookChannel, LogChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            WebhookChannel(
                port=port,
                path="/research",
                message_template=(
                    "An on-demand research request has arrived.\n\n"
                    "Payload:\n{payload}\n\n"
                    "Extract the 'query' or 'topic' field and research it now."
                ),
            )
        ],
        output_channels=[LogChannel()],
        require_buffer=False,
        thread_id="web-researcher-webhook",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_cron(topic: str, schedule: str):
    agent   = make_agent()
    runtime = build_cron_runtime(agent, topic, schedule)

    print(f"\n  Web Researcher  —  Scheduled mode")
    print(f"  {'─' * 44}")
    print(f"  Topic    : {topic}")
    print(f"  Schedule : {schedule}")
    print(f"  {'─' * 44}\n")

    await runtime.start()


async def run_webhook(port: int):
    agent   = make_agent()
    runtime = build_webhook_runtime(agent, port)

    print(f"\n  Web Researcher  —  Webhook mode")
    print(f"  {'─' * 44}")
    print(f"  Endpoint : http://localhost:{port}/research")
    print(f"  {'─' * 44}")
    print(f"\n  Send a research request:")
    print(f'    curl -X POST http://localhost:{port}/research \\')
    print(f'         -H "Content-Type: application/json" \\')
    print(f'         -d \'{{"query": "latest AI agent frameworks 2026"}}\'\n')

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Web Researcher — Tavily web search + cuga++ pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--topic",     "-t",
                        default=os.getenv("RESEARCH_TOPIC", "latest AI agent frameworks"))
    parser.add_argument("--schedule",  "-s", default="0 7 * * *",
                        help="Cron expression for scheduled mode (default: 7am daily)")
    parser.add_argument("--webhook",   action="store_true",
                        help="Run in webhook mode instead of scheduled cron mode")
    parser.add_argument("--port",      type=int, default=18791)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if not os.getenv("TAVILY_API_KEY"):
        print("\n  ⚠️  TAVILY_API_KEY not set — web_search tool will return an error.")
        print("  Get a free key at https://app.tavily.com and set:")
        print("  export TAVILY_API_KEY=tvly-...\n")

    if args.webhook:
        asyncio.run(run_webhook(args.port))
    else:
        asyncio.run(run_cron(args.topic, args.schedule))


if __name__ == "__main__":
    main()
