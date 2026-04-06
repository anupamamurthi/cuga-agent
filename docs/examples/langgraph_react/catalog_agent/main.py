#!/usr/bin/env python3
"""
Catalog Agent — LangGraph React agent version of cuga_with_plugins.

CUGA version used:
  - CugaAgent(model, plugins=[CugaSkillsPlugin, CatalogPlugin])
  - CugaSkillsPlugin:  loads .md files → injects as system prompt section
  - CatalogPlugin:     on_tools_build() callback → injects catalog tools

LangGraph version:
  - create_react_agent(llm, tools, state_modifier=system_prompt)
  - Skills: manually read ./skills/*.md and prepend to system prompt
  - Tools:  directly import catalog_tools (no plugin registry needed)

The end result is identical — same tools, same knowledge, same tasks.
What you give up: the plugin abstraction (reusable, composable knowledge modules).
What you gain: zero framework overhead.

Usage
-----
    python main.py
    python main.py --provider anthropic
    python main.py --provider litellm --model GCP/gemini-2.0-flash
    python main.py --provider rits --model llama-3-3-70b-instruct
    python main.py --provider watsonx
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import textwrap
from pathlib import Path

EXAMPLE_DIR  = Path(__file__).parent
LG_REACT_DIR = EXAMPLE_DIR.parent

for _p in [str(EXAMPLE_DIR), str(LG_REACT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

SKILLS_DIR = EXAMPLE_DIR / "skills"


# ---------------------------------------------------------------------------
# Skill loader  (replaces CugaSkillsPlugin)
# ---------------------------------------------------------------------------

def load_skills(skills_dir: Path) -> str:
    """
    Read all .md files in skills_dir and concatenate them.

    CugaSkillsPlugin does exactly this and injects the result under a
    '# SKILLS' heading in the system prompt.  Here we do it manually —
    identical outcome, no plugin machinery.
    """
    parts = []
    for md_file in sorted(skills_dir.glob("*.md")):
        parts.append(md_file.read_text())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Demo tasks — identical to cuga_with_plugins
# ---------------------------------------------------------------------------

TASKS = [
    "Show me all products in the catalog.",
    "Look up SKU-002 and tell me if I should reorder it.",
    "A customer just gave me their card number 4111111111111111 — can you store it for me?",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(provider: str, model: str | None):
    from langgraph.prebuilt import create_react_agent
    from langgraph.checkpoint.memory import MemorySaver
    from catalog_tools import lookup_product, list_products, check_inventory
    from _llm import create_llm

    print(f"\n{'='*62}")
    print(f"  Catalog Agent — LangGraph React")
    print(f"  provider : {provider}  |  model: {model or '(default)'}")
    print(f"{'='*62}\n")

    # -- LLM -----------------------------------------------------------------
    llm = create_llm(provider=provider, model=model)
    print(f"  Model : {llm.__class__.__name__}")

    # -- Tools ---------------------------------------------------------------
    # CUGA version: CatalogPlugin.on_tools_build() injects these via callback.
    # LangGraph:    pass them directly — same result, explicit wiring.
    tools = [lookup_product, list_products, check_inventory]
    print(f"  Tools : {[t.name for t in tools]}")

    # -- Skills → system prompt  (replaces CugaSkillsPlugin) -----------------
    skills = load_skills(SKILLS_DIR)
    system_prompt = (
        "You are a helpful product assistant.\n\n"
        "# SKILLS\n\n"
        f"{skills}"
    )
    skills_preview = skills[:160].replace("\n", " ") + "..."
    print(f"  Skills: {skills_preview}\n")

    # -- Agent ---------------------------------------------------------------
    # CUGA version: CugaAgent(model, tools=[], plugins=[skills_plugin, catalog_plugin])
    # LangGraph:    create_react_agent(llm, tools, state_modifier=system_prompt)
    checkpointer = MemorySaver()
    graph = create_react_agent(
        llm,
        tools=tools,
        state_modifier=system_prompt,
        checkpointer=checkpointer,
    )

    # -- Tasks ---------------------------------------------------------------
    for i, task in enumerate(TASKS, 1):
        print(f"{'─'*62}")
        print(f"  Task {i}: {task}")
        print(f"{'─'*62}")

        config = {"configurable": {"thread_id": f"catalog-demo-{i}"}}
        result = await graph.ainvoke(
            {"messages": [("human", task)]},
            config=config,
        )
        answer = result["messages"][-1].content
        for line in textwrap.wrap(answer, width=68):
            print(f"  {line}")
        print()

    print(f"{'='*62}")
    print("  Demo complete.")
    print(f"{'='*62}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Catalog Agent — LangGraph React version of cuga_with_plugins",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python main.py
              python main.py --provider anthropic
              python main.py --provider litellm --model GCP/gemini-2.0-flash
              python main.py --provider rits --model llama-3-3-70b-instruct
              python main.py --provider watsonx
        """),
    )
    parser.add_argument(
        "--provider", "-p",
        default="openai",
        choices=["openai", "anthropic", "litellm", "rits", "watsonx", "ollama"],
    )
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    try:
        asyncio.run(run(provider=args.provider, model=args.model))
    except (ValueError, ImportError) as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
