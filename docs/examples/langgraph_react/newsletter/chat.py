#!/usr/bin/env python3
"""
Newsletter — LangGraph React agent version.

CUGA version used:
  ChannelPlanner (internally uses CugaAgent) → NL → structured pipeline config
  CugaHost + CugaHostClient               → persistent background daemon
  CugaAgent                               → newsletter curation

LangGraph version replaces:
  ChannelPlanner  → llm.with_structured_output(PipelineConfig)
                    Pydantic schema enforces the same fields/defaults.
                    No agent loop needed — single LLM call with structured output.
  CugaHost        → asyncio background task (no HTTP daemon, no restart persistence)
  CugaAgent       → create_react_agent (in agent.py)

cuga++ channels still used:
  RssChannel   — RSS polling + keyword filtering
  EmailChannel — SMTP delivery
  LogChannel   — stdout fallback

Usage:
    python chat.py
    python chat.py "Monitor arxiv cs.AI for agent research, email me@example.com every hour"
    python chat.py --provider anthropic
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_EXAMPLE_DIR  = Path(__file__).parent
_LG_REACT_DIR = _EXAMPLE_DIR.parent
_SKILLS_DIR   = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_LG_REACT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("newsletter.chat")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

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
]

_RUNTIME_ID = "newsletter-monitor"


# ---------------------------------------------------------------------------
# Pipeline config schema  (replaces ChannelPlanner's PlannerTool schema)
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field


class PipelineConfig(BaseModel):
    """
    Structured output schema for newsletter pipeline configuration.

    CUGA version: ChannelPlanner.from_schema(...) with PlannerTool specs.
    LangGraph:    llm.with_structured_output(PipelineConfig)

    The field descriptions are what guide the LLM to extract the right values —
    same information that was in PlannerTool(params={...}) definitions.
    """
    intent: str = Field(
        default="monitor",
        description="User intent: 'monitor' (continuous), 'run_once' (one-shot fetch), "
                    "'stop' (stop running monitor), or 'status' (report status).",
    )
    sources: Optional[list[str]] = Field(
        default=None,
        description="RSS feed URLs to monitor. Use defaults if not mentioned.",
    )
    keywords: Optional[list[str]] = Field(
        default=None,
        description="Keyword filter terms. Use defaults if not mentioned.",
    )
    digest_minutes: Optional[int] = Field(
        default=None,
        description="Digest interval in minutes, extracted from natural language. "
                    "Examples: 'every hour' → 60, 'every 30 minutes' → 30, 'daily' → 1440. "
                    "Default: 240.",
    )
    poll_minutes: Optional[int] = Field(
        default=None,
        description="RSS polling interval in minutes. Default: 15.",
    )
    email: Optional[str] = Field(
        default=None,
        description="Recipient email address for delivery. Null means log to stdout.",
    )


def _apply_defaults(config: PipelineConfig) -> dict:
    return {
        "intent":         config.intent or "monitor",
        "sources":        config.sources  or list(_DEFAULT_SOURCES),
        "keywords":       config.keywords or list(_DEFAULT_KEYWORDS),
        "digest_minutes": config.digest_minutes or 240,
        "poll_minutes":   config.poll_minutes   or 15,
        "email":          config.email,
    }


def _build_planner(provider: str | None, model: str | None):
    """
    Build a structured-output LLM planner.

    CUGA version:
        ChannelPlanner.from_schema(llm, tools=[PlannerTool(...)])
        → full CugaAgent loop with tool-calling
        → returns PlannerResult with .config dict and .answer string

    LangGraph version:
        llm.with_structured_output(PipelineConfig)
        → single LLM call, no tool loop
        → returns PipelineConfig Pydantic model directly

    Tradeoff: CUGA ChannelPlanner can ask clarifying questions, explain itself,
    and maintain conversation history.  The structured_output approach is simpler
    and cheaper but less conversational.
    """
    from _llm import create_llm
    llm = create_llm(provider=provider, model=model)
    return llm.with_structured_output(PipelineConfig)


async def _extract_config(planner, utterance: str) -> PipelineConfig:
    """Run structured output extraction from a natural language utterance."""
    prompt = (
        "Extract a newsletter pipeline configuration from the following user request.\n\n"
        f"User: {utterance}\n\n"
        "If the user wants to stop or check status, set intent accordingly."
    )
    result = planner.invoke(prompt)
    return result


# ---------------------------------------------------------------------------
# One-shot run  (no background daemon)
# ---------------------------------------------------------------------------

async def run_once(config: dict, provider: str | None, model: str | None) -> None:
    """
    Fetch RSS items once, curate, and deliver.

    Uses cuga++ RssChannel for fetching and EmailChannel/LogChannel for delivery.
    Uses LangGraph React agent (agent.py) for curation.
    """
    from cuga_channels import EmailChannel, LogChannel, RssChannel
    from agent import make_agent, curate

    print("\nFetching RSS feeds…")
    items = await RssChannel.fetch_once(
        sources=config["sources"],
        keywords=config["keywords"],
    )
    if not items:
        print("No matching items found.")
        return

    items = items[:20]
    print(f"Found {len(items)} item(s). Curating…\n")

    graph = make_agent()
    html = await curate(graph, items, thread_id="newsletter-run-once")

    email      = config.get("email")
    smtp_ready = bool(email and os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))

    ch = (
        EmailChannel(
            to=email,
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            subject_prefix="Newsletter",
        )
        if smtp_ready
        else LogChannel()
    )
    await ch.deliver(html, {"item_count": len(items)})


# ---------------------------------------------------------------------------
# Background monitor  (replaces CugaHost daemon)
# ---------------------------------------------------------------------------

_monitor_task: asyncio.Task | None = None
_monitor_config: dict | None = None


async def _monitor_loop(config: dict, provider: str | None, model: str | None):
    """
    Continuous RSS monitor — fetch on poll interval, curate on digest interval.

    CUGA version: CugaHost manages CugaRuntime with RssChannel + CronChannel +
                  ChannelBuffer + EmailChannel — all running as coordinated tasks
                  that survive process restarts (persisted in runtimes.json).

    LangGraph version: single asyncio task.  No restart persistence, no
    HTTP API for reconfiguration at runtime.
    """
    from cuga_channels import EmailChannel, LogChannel, RssChannel
    from agent import make_agent, curate

    graph = make_agent()
    poll_s   = config["poll_minutes"]   * 60
    digest_s = config["digest_minutes"] * 60
    buffer: list[dict] = []
    last_digest = asyncio.get_event_loop().time()

    logger.info(
        "Monitor started — %d source(s), poll %dmin, digest %dmin, delivery → %s",
        len(config["sources"]),
        config["poll_minutes"],
        config["digest_minutes"],
        config.get("email") or "stdout",
    )

    while True:
        # Poll feeds
        try:
            items = await RssChannel.fetch_once(
                sources=config["sources"],
                keywords=config["keywords"],
            )
            new_items = [i for i in items if i not in buffer]
            buffer.extend(new_items)
            if new_items:
                logger.info("Buffered %d new item(s) (total: %d)", len(new_items), len(buffer))
        except Exception:
            logger.exception("RSS fetch error")

        # Digest if interval elapsed and buffer has items
        now = asyncio.get_event_loop().time()
        if buffer and (now - last_digest) >= digest_s:
            try:
                send_items = buffer[:20]
                buffer.clear()
                last_digest = now
                logger.info("Digest firing — %d item(s)", len(send_items))

                html = await curate(graph, send_items)

                email      = config.get("email")
                smtp_ready = bool(email and os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))
                ch = (
                    EmailChannel(
                        to=email,
                        smtp_username=os.getenv("SMTP_USERNAME", ""),
                        smtp_password=os.getenv("SMTP_PASSWORD", ""),
                        subject_prefix="Newsletter",
                    )
                    if smtp_ready
                    else LogChannel()
                )
                await ch.deliver(html, {"item_count": len(send_items)})
            except Exception:
                logger.exception("Digest error")

        await asyncio.sleep(poll_s)


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

async def interactive_loop(provider: str | None, model: str | None) -> None:
    global _monitor_task, _monitor_config

    print()
    print("Newsletter — LangGraph React (type your intent, or 'status' / 'stop')")
    print()
    print("Examples:")
    print('  "Monitor arxiv cs.AI for agent research, email me@example.com every hour"')
    print('  "Fetch AI news from https://arxiv.org/rss/cs.AI and summarise"')
    print('  "Watch HuggingFace and VentureBeat for LLM news every 30 minutes"')
    print('  "status"  /  "stop"')
    print()

    planner = _build_planner(provider, model)

    while True:
        try:
            utterance = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not utterance:
            continue
        if utterance.lower() in {"quit", "exit", "bye"}:
            print("Bye.")
            break

        # Simple shortcuts (bypass LLM for unambiguous commands)
        if utterance.lower() == "status":
            if _monitor_task and not _monitor_task.done():
                cfg = _monitor_config or {}
                print(
                    f"Agent: Monitor active — {len(cfg.get('sources', []))} feed(s), "
                    f"every {cfg.get('digest_minutes', '?')} min, "
                    f"delivery → {cfg.get('email') or 'stdout'}.\n"
                )
            else:
                print("Agent: No active monitor.\n")
            continue

        if utterance.lower() == "stop":
            if _monitor_task and not _monitor_task.done():
                _monitor_task.cancel()
                _monitor_task = None
                print("Agent: Monitor stopped.\n")
            else:
                print("Agent: No active monitor to stop.\n")
            continue

        # NL → structured config via llm.with_structured_output
        try:
            config_obj = await _extract_config(planner, utterance)
        except Exception as e:
            print(f"Could not parse intent: {e}\n")
            continue

        config = _apply_defaults(config_obj)
        print(f"Agent: Understood — intent={config_obj.intent}\n")

        if config_obj.intent == "run_once":
            await run_once(config, provider, model)

        elif config_obj.intent == "monitor":
            if _monitor_task and not _monitor_task.done():
                _monitor_task.cancel()
            _monitor_config = config
            _monitor_task = asyncio.create_task(
                _monitor_loop(config, provider, model)
            )
            print(
                f"  Monitor running — {len(config['sources'])} feed(s), "
                f"every {config['digest_minutes']} min, Ctrl+C to stop.\n"
            )

        elif config_obj.intent == "stop":
            if _monitor_task and not _monitor_task.done():
                _monitor_task.cancel()
                _monitor_task = None
                print("Agent: Monitor stopped.\n")
            else:
                print("Agent: No active monitor.\n")

        elif config_obj.intent == "status":
            if _monitor_task and not _monitor_task.done():
                cfg = _monitor_config or {}
                print(
                    f"Agent: Monitor active — {len(cfg.get('sources', []))} feed(s), "
                    f"every {cfg.get('digest_minutes', '?')} min, "
                    f"delivery → {cfg.get('email') or 'stdout'}.\n"
                )
            else:
                print("Agent: No active monitor.\n")

    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Newsletter — LangGraph React agent version",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python chat.py
              python chat.py "Fetch AI news from https://arxiv.org/rss/cs.AI"
              python chat.py "Monitor arxiv for agent research, email me@x.com every hour"
              python chat.py --provider anthropic "Watch VentureBeat every 30 minutes"
        """),
    )
    parser.add_argument("utterance", nargs="?", default=None)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.utterance:
        async def _run():
            planner = _build_planner(args.provider, args.model)
            config_obj = await _extract_config(planner, args.utterance)
            config = _apply_defaults(config_obj)

            if config_obj.intent == "run_once":
                await run_once(config, args.provider, args.model)
            else:
                print(f"\nStarting monitor — {config_obj.intent}")
                print(f"  Sources  : {len(config['sources'])} feed(s)")
                print(f"  Digest   : every {config['digest_minutes']} min")
                print(f"  Delivery : {config.get('email') or 'stdout'}")
                print("  Ctrl+C to stop.\n")
                try:
                    await _monitor_loop(config, args.provider, args.model)
                except asyncio.CancelledError:
                    pass
        asyncio.run(_run())
    else:
        asyncio.run(interactive_loop(args.provider, args.model))


if __name__ == "__main__":
    main()
