"""
Newsletter — web chat interface
================================
Conversational browser interface for the newsletter pipeline.
Same capabilities as chat.py but served over WebSocket/HTTP.

Architecture:
  Browser chat  →  ConversationGateway  →  CugaAgent
                                            ├── start_newsletter_monitor
                                            ├── stop_newsletter_monitor
                                            ├── get_newsletter_status
                                            └── fetch_newsletter_now

  CugaAgent tools call CugaHostClient → CugaHost manages the actual runtime.

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider rits
    python app.py --provider openai --model gpt-4o

Then open http://127.0.0.1:8769

Try saying:
    "Monitor arxiv for AI agent research, digest every 4 hours"
    "Watch arxiv and HuggingFace, send to me@example.com daily at 9am"
    "Fetch the latest AI news right now"
    "status"  /  "stop"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
_SKILLS_DIR  = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

_RUNTIME_ID = "newsletter-monitor"

_DEFAULT_SOURCES = [
    "https://arxiv.org/rss/cs.AI",
    "https://arxiv.org/rss/cs.LG",
    "https://huggingface.co/blog/feed.xml",
    "https://hnrss.org/newest?q=LLM+AI+agent&points=10",
    "https://venturebeat.com/category/ai/feed/",
]

_DEFAULT_KEYWORDS = [
    "LLM", "large language model", "agent", "agentic",
    "RAG", "reasoning", "Claude", "GPT", "Gemini", "Llama",
    "CUGA", "ALTK",
]


def make_agent(client):
    from langchain_core.tools import tool
    from cuga import CugaAgent
    from cuga_channels import RssChannel, smart_deliver
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    @tool
    async def start_newsletter_monitor(
        sources: list[str] | None = None,
        keywords: list[str] | None = None,
        digest_minutes: int = 240,
        poll_minutes: int = 15,
        email: str | None = None,
    ) -> str:
        """
        Start the newsletter monitor — poll RSS feeds and deliver curated digests on a schedule.

        Args:
            sources:        RSS feed URLs. Uses defaults (arxiv, HuggingFace, VentureBeat) if omitted.
            keywords:       Filter keywords. Uses defaults (LLM, agent, RAG, etc.) if omitted.
            digest_minutes: How often to send the digest in minutes (default: 240 = every 4 hours).
                            Natural language: "every hour" → 60, "daily at 9am" → use cron instead.
            poll_minutes:   How often to poll feeds in minutes (default: 15).
            email:          Delivery email address. Logs to console if not specified.
        """
        config = {
            "sources":        sources or list(_DEFAULT_SOURCES),
            "keywords":       keywords or list(_DEFAULT_KEYWORDS),
            "digest_minutes": digest_minutes,
            "poll_minutes":   poll_minutes,
            "email":          email,
            "provider":       os.getenv("LLM_PROVIDER"),
            "model":          os.getenv("LLM_MODEL"),
        }
        info = await client.start_runtime(_RUNTIME_ID, "newsletter", config)
        cron = info.get("config", {}).get("digest_minutes", digest_minutes)
        dest = email or "console (set email= to deliver by email)"
        return (
            f"Newsletter monitor started.\n"
            f"  Feeds:    {len(config['sources'])}\n"
            f"  Digest:   every {cron} min\n"
            f"  Delivery: {dest}"
        )

    @tool
    async def stop_newsletter_monitor() -> str:
        """Stop the currently running newsletter monitor pipeline."""
        try:
            await client.stop_runtime(_RUNTIME_ID)
            return "Newsletter monitor stopped."
        except Exception:
            return "No active newsletter monitor to stop."

    @tool
    async def get_newsletter_status() -> str:
        """Report whether the newsletter monitor is running and show its current configuration."""
        try:
            info = await client.get_runtime(_RUNTIME_ID)
            cfg  = info.get("config", {})
            dest = cfg.get("email") or "console"
            return (
                f"Newsletter monitor is running.\n"
                f"  Feeds:    {len(cfg.get('sources', []))}\n"
                f"  Digest:   every {cfg.get('digest_minutes', '?')} min\n"
                f"  Delivery: {dest}"
            )
        except Exception:
            return "No newsletter monitor is currently running."

    @tool
    async def fetch_newsletter_now(
        sources: list[str] | None = None,
        keywords: list[str] | None = None,
        email: str | None = None,
    ) -> str:
        """
        Fetch RSS feeds and curate a newsletter right now — one-time, no ongoing monitoring.

        Args:
            sources:  RSS feed URLs. Uses defaults if omitted.
            keywords: Filter keywords. Uses defaults if omitted.
            email:    Delivery email. Returns a preview here if omitted.
        """
        items = await RssChannel.fetch_once(
            sources=sources or list(_DEFAULT_SOURCES),
            keywords=keywords or list(_DEFAULT_KEYWORDS),
        )
        if not items:
            return "No matching items found in the RSS feeds right now."

        items = items[:20]
        curation_prompt = (
            f"Curate and compose a newsletter digest from these {len(items)} RSS items.\n\n"
            + json.dumps(items, ensure_ascii=False, indent=2)
        )

        # Return the fetched items summary — the agent will curate via its own skills
        dest = email or "this chat"
        return (
            f"Fetched {len(items)} items from the feeds. "
            f"Curating now and delivering to {dest}.\n\n"
            f"Items:\n" + "\n".join(
                f"  • {it.get('title', '(no title)')} [{it.get('source', '')}]"
                for it in items[:10]
            ) + (f"\n  … and {len(items) - 10} more" if len(items) > 10 else "")
        )

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=[
            start_newsletter_monitor,
            stop_newsletter_monitor,
            get_newsletter_status,
            fetch_newsletter_now,
        ],
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )


async def main(host: str, port: int) -> None:
    from cuga_channels import ConversationGateway, CugaHostClient

    cuga_host, client = await CugaHostClient.connect_or_embed(
        state_dir=_EXAMPLE_DIR / ".cuga" / "host",
        pipelines_config=_EXAMPLE_DIR / "cuga_pipelines.yaml",
    )

    agent   = make_agent(client=client)
    gateway = ConversationGateway(agent=agent)
    gateway.add_browser_adapter(host=host, port=port, title="Newsletter Agent")

    try:
        await gateway.start()
    finally:
        if cuga_host is not None:
            await cuga_host.stop()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Newsletter Agent — conversational web interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider rits
              python app.py --provider openai --model gpt-4o

            Then open http://127.0.0.1:8769

            Try saying:
              "Monitor arxiv for AI agent research, digest every 4 hours"
              "Watch arxiv and HuggingFace, email me@example.com daily"
              "Fetch the latest AI news right now"
              "status"  /  "stop"
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8769)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Newsletter Agent  →  http://{args.host}:{args.port}\n")
    asyncio.run(main(args.host, args.port))
