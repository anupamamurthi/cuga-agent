#!/usr/bin/env python3
"""
CugaAgent + cuga++ plugins demo
================================

Demonstrates two plugin capabilities wired into CugaAgent:

  1. CugaSkillsPlugin  — loads .md files from ./skills/ and injects them
                         as a # SKILLS section in the system prompt
  2. CatalogPlugin     — registers product catalog tools via on_tools_build
                         (lookup_product, list_products, check_inventory)

The agent receives both the skill instructions AND the tools at init time,
entirely through the plugin registry — no direct tool or prompt wiring needed.

Usage
-----
# Default (reads OPENAI_API_KEY from environment)
uv run python main.py

# Choose a different provider
uv run python main.py --provider anthropic
uv run python main.py --provider litellm --model GCP/gemini-2.0-flash
uv run python main.py --provider rits --model llama-3-3-70b-instruct
uv run python main.py --provider watsonx

Environment variables
----------------------
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  LITELLM_API_KEY, LITELLM_BASE_URL
  RITS_API_KEY
  WATSONX_APIKEY, WATSONX_PROJECT_ID, WATSONX_SPACE_ID, WATSONX_URL
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Env setup — must happen before cuga imports
# ---------------------------------------------------------------------------
os.environ.setdefault("DYNA_CONF_ADVANCED_FEATURES__MODE", "api")
os.environ.setdefault("DYNA_CONF_FEATURES__LOCAL_SANDBOX", "true")

EXAMPLE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# CatalogPlugin — contributes catalog tools via on_tools_build
# ---------------------------------------------------------------------------

class CatalogPlugin:
    """
    Plugin that registers product catalog tools into CugaAgent at init time.

    Implements both CugaPlugin (on_prompt_build) and ToolPlugin (on_tools_build)
    from cuga-plugin-sdk — no imports from cuga internals required.
    """
    name = "catalog-plugin"
    version = "0.1.0"

    def on_prompt_build(self, context):
        # No extra prompt content from this plugin — skills handle the presentation rules
        return None

    def on_tools_build(self, context):
        from cuga_plugin_sdk import ToolContribution
        from catalog_tools import lookup_product, list_products, check_inventory
        return ToolContribution(tools=[lookup_product, list_products, check_inventory])


# ---------------------------------------------------------------------------
# Demo tasks
# ---------------------------------------------------------------------------

TASKS = [
    "Show me all products in the catalog.",
    "Look up SKU-002 and tell me if I should reorder it.",
    "A customer just gave me their card number 4111111111111111 — can you store it for me?",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(provider: str, model: str | None, **kwargs):
    from cuga.sdk import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from cuga_runtime.llm import create_llm

    # -- LLM -----------------------------------------------------------------
    print(f"\n{'='*62}")
    print(f"  CugaAgent + cuga++ plugins demo")
    print(f"  provider : {provider}  |  model: {model or '(default)'}")
    print(f"{'='*62}\n")

    llm = create_llm(provider=provider, model=model, **kwargs)
    print(f"  Model      : {llm.__class__.__name__}")

    # -- Plugins -------------------------------------------------------------
    skills_plugin = CugaSkillsPlugin(skills_dir=str(EXAMPLE_DIR / "skills"))
    catalog_plugin = CatalogPlugin()

    # -- Agent ---------------------------------------------------------------
    agent = CugaAgent(
        model=llm,
        plugins=[skills_plugin, catalog_plugin],
        auto_load_policies=False,
    )
    await agent.initialize()

    # Show what was loaded
    tool_names = [t.name for t in agent.tool_provider.tools]
    skills_preview = agent._skills[:180].replace("\n", " ") + "..." if agent._skills else "(none)"
    print(f"  Tools      : {tool_names}")
    print(f"  Skills     : {skills_preview}\n")

    # -- Tasks ---------------------------------------------------------------
    thread_id = "plugins-demo"
    for i, task in enumerate(TASKS, 1):
        print(f"{'─'*62}")
        print(f"  Task {i}: {task}")
        print(f"{'─'*62}")
        result = await agent.invoke(task, thread_id=f"{thread_id}-{i}")
        for line in textwrap.wrap(str(result), width=68):
            print(f"  {line}")
        print()

    print(f"{'='*62}")
    print("  Demo complete.")
    print(f"{'='*62}\n")


def main():
    parser = argparse.ArgumentParser(
        description="CugaAgent + cuga++ plugins demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              uv run python main.py
              uv run python main.py --provider anthropic
              uv run python main.py --provider litellm --model GCP/gemini-2.0-flash
              uv run python main.py --provider rits --model llama-3-3-70b-instruct
              uv run python main.py --provider watsonx
        """),
    )
    parser.add_argument(
        "--provider", "-p",
        default="openai",
        choices=["openai", "anthropic", "litellm", "rits", "watsonx", "ollama"],
        help="LLM provider (default: openai)",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model name override",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="Custom API base URL (litellm / ollama)",
    )
    args = parser.parse_args()

    kwargs = {}
    if args.api_base:
        kwargs["api_base"] = args.api_base
        kwargs["ollama_base_url"] = args.api_base

    try:
        asyncio.run(run(provider=args.provider, model=args.model, **kwargs))
    except (ValueError, ImportError) as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
