#!/usr/bin/env python3
"""
Newsletter — conversational interface
======================================
The user speaks naturally.  CUGA interprets.  CugaHost manages the runtime.

Architecture (OpenClaw-aligned):

  CugaHost  (daemon, always on — cuga++ gateway equivalent)
  ├── owns CugaRuntime instances (channels, cron jobs)
  ├── persists runtime configs to ~/.cuga/host/runtimes.json
  ├── restores runtimes on restart (like OpenClaw restoring jobs.json)
  └── exposes HTTP API on port 18790

  ChannelPlanner  (NL → structured config)
  ├── HOST_PROMPT describes available channels and extraction rules
  ├── raw utterance → CUGA planning agent → configure_monitor / stop / status tool
  └── returns PlannerResult.config — fully serializable dict

  chat.py  (thin client — like the OpenClaw CLI)
  ├── starts CugaHost embedded (background task)
  ├── builds ChannelPlanner
  └── utterance → planner → PlannerResult → CugaHostClient.start/stop_runtime

Usage
-----
    python docs/examples/demo_apps/newsletter/chat.py
    python docs/examples/demo_apps/newsletter/chat.py \\
        "Monitor arxiv cs.AI for agent research, email me@example.com every hour"
    python docs/examples/demo_apps/newsletter/chat.py --provider anthropic
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
    "CUGA", "ALTK",
]

_DIGEST_MESSAGE = (
    "It's time to send the newsletter digest.\n\n"
    "You have been given a list of RSS items collected since the last digest.\n"
    "Select the most significant and diverse items, avoiding duplicates from prior runs.\n"
    "Compose a styled HTML newsletter using the newsletter_curation skill.\n\n"
    "Your full output will be delivered automatically — do not call any send or email tools."
)

_RUNTIME_ID = "newsletter-monitor"

# ---------------------------------------------------------------------------
# HOST PROMPT  (describes cuga++ channels — injected before every utterance)
# ---------------------------------------------------------------------------

def _build_planner(provider: str | None, model: str | None):
    from cuga_channels import ChannelPlanner, PlannerTool
    from _llm import create_llm

    return ChannelPlanner.from_schema(
        llm=create_llm(provider=provider, model=model),
        tools=[
            PlannerTool(
                name="configure_monitor",
                description=(
                    "Configure the newsletter pipeline. Call this when the user wants "
                    "to start monitoring RSS feeds or run a one-time fetch."
                ),
                params={
                    "intent":         (str,       "monitor", "monitor | run_once"),
                    "sources":        (list[str], None,      "RSS feed URLs; use defaults if not mentioned"),
                    "keywords":       (list[str], None,      "filter terms; use defaults if not mentioned"),
                    "digest_minutes": (int,       240,       "digest interval extracted from natural language"),
                    "poll_minutes":   (int,       15,        "RSS polling interval in minutes"),
                    "email":          (str,       None,      "recipient email address; null to log to stdout"),
                },
            ),
            PlannerTool("stop_monitor", "Stop the currently running newsletter monitor."),
            PlannerTool("get_status",   "Report whether a monitor is currently running."),
        ],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga" / "planning"),
        thread_id="newsletter-planner",
    )

# ---------------------------------------------------------------------------
# Config post-processing — fill None fields with defaults + embed provider
# ---------------------------------------------------------------------------

def _apply_defaults(config: dict, provider: str | None, model: str | None) -> dict:
    """Fill None fields from PlannerResult.config with app defaults."""
    return {
        "intent":         config.get("intent", "monitor"),
        "sources":        config.get("sources")   or list(_DEFAULT_SOURCES),
        "keywords":       config.get("keywords")  or list(_DEFAULT_KEYWORDS),
        "digest_minutes": config.get("digest_minutes") or 240,
        "poll_minutes":   config.get("poll_minutes")   or 15,
        "email":          config.get("email"),
        "provider":       provider,
        "model":          model,
    }


# (RuntimeFactory lives in host_factories.py — loaded by CugaHost)

# ---------------------------------------------------------------------------
# One-shot run  (no CugaHost — fetch once, deliver once, done)
# ---------------------------------------------------------------------------

async def run_once(config: dict, provider: str | None, model: str | None) -> None:
    from cuga import CugaAgent
    from cuga_channels import EmailChannel, LogChannel, RssChannel
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    print("\nFetching RSS feeds…")
    items = await RssChannel.fetch_once(
        sources=config["sources"],
        keywords=config["keywords"],
    )
    if not items:
        print("No matching items found.")
        return

    print(f"Found {len(items)} item(s). Curating…\n")

    agent = CugaAgent(
        model=create_llm(provider=provider, model=model),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )
    message = (
        f"Curate and compose an HTML newsletter from the following RSS items.\n\n"
        f"Items ({len(items)}):\n"
        f"{json.dumps(items, ensure_ascii=False, indent=2)}"
    )
    result = await agent.invoke(message, thread_id="newsletter-run-once")

    email      = config.get("email")
    smtp_ready = bool(email and os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))

    ch = (
        EmailChannel(
            to=email,
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            subject_prefix="CUGA Newsletter",
        )
        if smtp_ready
        else LogChannel()
    )
    await ch.deliver(result.answer, {"item_count": len(items)})

# (host connection is handled by CugaHostClient.connect_or_embed)


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

async def interactive_loop(provider: str | None, model: str | None) -> None:
    print()
    print("CUGA Newsletter — just describe what you want.")
    print()
    print("Examples:")
    print('  "Monitor arxiv cs.AI for agent research, email me@example.com every hour"')
    print('  "Fetch AI news from https://arxiv.org/rss/cs.AI and summarise"')
    print('  "Watch HuggingFace and VentureBeat for LLM news every 30 minutes"')
    print('  "status"  /  "stop"')
    print()

    from cuga_channels import CugaHostClient
    host, client = await CugaHostClient.connect_or_embed(
        state_dir=_EXAMPLE_DIR / ".cuga" / "host",
        pipelines_config=_EXAMPLE_DIR / "cuga_pipelines.yaml",
    )
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

        # ── raw utterance → ChannelPlanner → PlannerResult ──────────────
        pr = await planner.invoke(utterance)
        if pr.answer:
            print(f"CUGA: {pr.answer}\n")

        # ── cuga++ (CugaHost) acts on the structured result ──────────────
        if pr.config:
            config = _apply_defaults(pr.config, provider, model)

            if config["intent"] == "run_once":
                await run_once(config, provider, model)

            elif config["intent"] == "monitor":
                info = await client.start_runtime(_RUNTIME_ID, "newsletter", config)
                cron = info.get("config", {}).get("digest_minutes", 240)
                print(
                    f"  Monitor running — {len(config['sources'])} feed(s), "
                    f"every {cron} min, Ctrl+C to stop.\n"
                )

        elif pr.action == "stop":
            try:
                await client.stop_runtime(_RUNTIME_ID)
                print("CUGA: Monitor stopped.\n")
            except Exception:
                print("CUGA: No active monitor to stop.\n")

        elif pr.action == "status":
            try:
                info = await client.get_runtime(_RUNTIME_ID)
                cfg  = info.get("config", {})
                dest = cfg.get("email") or "stdout"
                print(
                    f"CUGA: Monitor active — {len(cfg.get('sources', []))} feed(s), "
                    f"every {cfg.get('digest_minutes', '?')} min, delivery → {dest}.\n"
                )
            except Exception:
                print("CUGA: No active monitor.\n")

    if host:
        await host.stop()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Newsletter agent — OpenClaw-style NL interface via CugaHost",
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
            from cuga_channels import CugaHostClient
            host, client = await CugaHostClient.connect_or_embed(
                state_dir=_EXAMPLE_DIR / ".cuga" / "host",
                pipelines_config=_EXAMPLE_DIR / "cuga_pipelines.yaml",
            )
            planner = _build_planner(args.provider, args.model)
            pr      = await planner.invoke(args.utterance)

            if pr.answer:
                print(f"CUGA: {pr.answer}\n")

            if not pr.config:
                print("Could not determine intent. Try rephrasing.")
                if host:
                    await host.stop()
                return

            config = _apply_defaults(pr.config, args.provider, args.model)
            if config["intent"] == "run_once":
                await run_once(config, args.provider, args.model)
                if host:
                    await host.stop()
            else:
                await client.start_runtime(_RUNTIME_ID, "newsletter", config)
                cron = config.get("digest_minutes", 240)
                print("=" * 62)
                print("  CUGA Newsletter Monitor — running")
                print(f"  Sources  : {len(config['sources'])} feed(s)")
                print(f"  Digest   : every {cron} min")
                print(f"  Delivery : {config.get('email') or 'stdout'}")
                print("  Ctrl+C to stop.")
                print("=" * 62)
                await asyncio.get_event_loop().create_future()

        asyncio.run(_run())
    else:
        asyncio.run(interactive_loop(args.provider, args.model))


if __name__ == "__main__":
    main()
