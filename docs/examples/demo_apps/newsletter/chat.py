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

_HOST_PROMPT = """\
You are a newsletter monitor assistant backed by cuga++ infrastructure.

Available cuga++ channels
--------------------------
DataChannels  (continuously collect data):
  RssChannel   — polls RSS/Atom feeds, filters by keywords, buffers matched items

TriggerChannels  (wake the agent on a schedule):
  CronChannel  — fires on a cron schedule (e.g. every 4 hours, every 30 minutes)

OutputChannels  (deliver agent output — the agent never decides delivery):
  EmailChannel — sends the curated HTML digest via SMTP
  LogChannel   — prints to stdout (used when no email address is configured)

Your job
--------
Interpret the user's request and call one of the planning tools:

  configure_monitor(intent, sources, keywords, digest_minutes, poll_minutes, email)
    - intent "monitor"  → start continuous monitoring pipeline
    - intent "run_once" → fetch now and produce one digest immediately

  stop_monitor()   → stop the currently running monitor
  get_status()     → report what is currently running

After calling the tool, confirm in one friendly sentence what you configured.

Defaults (use when the user does not specify)
---------------------------------------------
  sources:        arxiv cs.AI, arxiv cs.LG, HuggingFace Blog, HN AI, VentureBeat AI
  keywords:       LLM, agent, agentic, reasoning, RAG, Claude, GPT, Gemini, Llama, CUGA, ALTK
  digest_minutes: 240  (every 4 hours)
  poll_minutes:   15

Extracting digest_minutes from natural language
-----------------------------------------------
  "every 5 minutes"  →    5
  "every 30 minutes" →   30
  "every hour"       →   60
  "every 2 hours"    →  120
  "every 4 hours"    →  240   ← default
  "twice a day"      →  720
  "daily"            → 1440

Extracting email
----------------
  Extract any email address mentioned. If none, use null — LogChannel will be used.
"""

# ---------------------------------------------------------------------------
# Newsletter-specific planning tools
# ---------------------------------------------------------------------------

def _make_planning_tools(state: dict) -> list:
    from langchain_core.tools import tool

    @tool
    def configure_monitor(
        intent: str,
        sources: list[str] | None = None,
        keywords: list[str] | None = None,
        digest_minutes: int = 240,
        poll_minutes: int = 15,
        email: str | None = None,
    ) -> str:
        """
        Configure the cuga++ newsletter pipeline.

        Args:
            intent:         "monitor" for continuous, "run_once" for immediate one-shot.
            sources:        RSS feed URLs. Use defaults if not mentioned.
            keywords:       Filter terms. Use defaults if not mentioned.
            digest_minutes: Digest interval extracted from natural language.
            poll_minutes:   RSS poll interval (default 15).
            email:          Recipient address if mentioned; null to log to stdout.
        """
        state["config"] = {
            "intent":         intent,
            "sources":        sources  or list(_DEFAULT_SOURCES),
            "keywords":       keywords or list(_DEFAULT_KEYWORDS),
            "digest_minutes": digest_minutes,
            "poll_minutes":   poll_minutes,
            "email":          email,
        }
        dest = f"→ {email}" if email else "→ stdout"
        return (
            f"{'Monitor' if intent == 'monitor' else 'One-shot'} configured: "
            f"{len(state['config']['sources'])} source(s), "
            f"every {digest_minutes} min, {dest}."
        )

    @tool
    def stop_monitor() -> str:
        """Stop the currently running newsletter monitor."""
        state["action"] = "stop"
        return "Stop requested."

    @tool
    def get_status() -> str:
        """Report whether a monitor is currently running."""
        state["action"] = "status"
        return "Status requested."

    return [configure_monitor, stop_monitor, get_status]

# ---------------------------------------------------------------------------
# ChannelPlanner builder
# ---------------------------------------------------------------------------

def _build_planner(provider: str | None, model: str | None):
    from cuga import CugaAgent
    from cuga_channels import ChannelPlanner
    from _llm import create_llm

    state = {}
    agent = CugaAgent(
        model=create_llm(provider=provider, model=model),
        tools=_make_planning_tools(state),
        cuga_folder=str(_EXAMPLE_DIR / ".cuga" / "planning"),
    )
    return ChannelPlanner(
        agent=agent,
        host_prompt=_HOST_PROMPT,
        state=state,
        thread_id="newsletter-planner",
    )

# ---------------------------------------------------------------------------
# RuntimeFactory  (registered with CugaHost — builds CugaRuntime from config)
#
# This is the app-specific factory.  CugaHost is generic; it calls this
# function when it needs to build or restore a newsletter runtime.
# The config dict is fully serializable — the host can persist and restore it.
# ---------------------------------------------------------------------------

def newsletter_runtime_factory(config: dict):
    """
    Build a CugaRuntime for the newsletter pipeline from a plain config dict.

    Called by CugaHost when:
      - a new runtime is started (via CugaHostClient.start_runtime)
      - a runtime is restored from runtimes.json on host startup
    """
    from cuga import CugaAgent
    from cuga_channels import (
        CronChannel, CugaRuntime, EmailChannel, LogChannel, RssChannel,
    )
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    provider       = config.get("provider")
    model          = config.get("model")
    sources        = config.get("sources",        list(_DEFAULT_SOURCES))
    keywords       = config.get("keywords",       list(_DEFAULT_KEYWORDS))
    digest_minutes = config.get("digest_minutes", 240)
    poll_minutes   = config.get("poll_minutes",   15)
    email          = config.get("email")

    def _interval_to_cron(minutes: int) -> str:
        return f"*/{minutes} * * * *" if minutes < 60 else f"0 */{minutes // 60} * * *"

    smtp_ready = bool(
        email and os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD")
    )

    agent = CugaAgent(
        model=create_llm(provider=provider, model=model),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )

    output_channels = (
        [
            EmailChannel(
                to=email,
                smtp_username=os.getenv("SMTP_USERNAME", ""),
                smtp_password=os.getenv("SMTP_PASSWORD", ""),
                subject_prefix="CUGA Newsletter",
            ),
            LogChannel(),
        ]
        if smtp_ready
        else [LogChannel()]
    )

    return CugaRuntime(
        agent=agent,
        input_channels=[
            RssChannel(sources=sources, keywords=keywords, poll_minutes=poll_minutes),
            CronChannel(
                schedule=_interval_to_cron(digest_minutes),
                message=_DIGEST_MESSAGE,
                name="digest-cron",
            ),
        ],
        output_channels=output_channels,
        thread_id="newsletter-digest",
    )

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

# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

async def interactive_loop(provider: str | None, model: str | None) -> None:
    from cuga_channels import CugaHost, CugaHostClient

    print()
    print("CUGA Newsletter — just describe what you want.")
    print()
    print("Examples:")
    print('  "Monitor arxiv cs.AI for agent research, email me@example.com every hour"')
    print('  "Fetch AI news from https://arxiv.org/rss/cs.AI and summarise"')
    print('  "Watch HuggingFace and VentureBeat for LLM news every 30 minutes"')
    print('  "status"  /  "stop"')
    print()

    # Start CugaHost embedded (background task — like OpenClaw daemon)
    host = CugaHost(state_dir=_EXAMPLE_DIR / ".cuga" / "host")
    host.register_factory("newsletter", newsletter_runtime_factory)
    await host.start_background()

    client  = CugaHostClient()
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
            config = pr.config

            if config["intent"] == "run_once":
                await run_once(config, provider, model)

            elif config["intent"] == "monitor":
                # Embed provider/model so the factory can rebuild on restore
                config["provider"] = provider
                config["model"]    = model
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
            from cuga_channels import CugaHost, CugaHostClient

            host = CugaHost(state_dir=_EXAMPLE_DIR / ".cuga" / "host")
            host.register_factory("newsletter", newsletter_runtime_factory)
            await host.start_background()

            client  = CugaHostClient()
            planner = _build_planner(args.provider, args.model)
            pr      = await planner.invoke(args.utterance)

            if pr.answer:
                print(f"CUGA: {pr.answer}\n")

            if not pr.config:
                print("Could not determine intent. Try rephrasing.")
                await host.stop()
                return

            config = pr.config
            if config["intent"] == "run_once":
                await run_once(config, args.provider, args.model)
                await host.stop()
            else:
                config["provider"] = args.provider
                config["model"]    = args.model
                await client.start_runtime(_RUNTIME_ID, "newsletter", config)
                cron = config.get("digest_minutes", 240)
                print("=" * 62)
                print("  CUGA Newsletter Monitor — running")
                print(f"  Sources  : {len(config['sources'])} feed(s)")
                print(f"  Digest   : every {cron} min")
                print(f"  Delivery : {config.get('email') or 'stdout'}")
                print("  Ctrl+C to stop.")
                print("=" * 62)
                # Block until signal (CugaHost.start() handles SIGINT)
                await asyncio.get_event_loop().create_future()

        asyncio.run(_run())
    else:
        asyncio.run(interactive_loop(args.provider, args.model))


if __name__ == "__main__":
    main()
