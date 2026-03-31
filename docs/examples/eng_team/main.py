#!/usr/bin/env python3
"""
Engineering Team — Multi-Agent Demo
=====================================

Five CUGA agents simulate a real engineering team processing a feature request:

  PM      — writes the spec (user story + ACs + estimate)
  Dev     — designs the implementation (API + approach + patterns)
  QA      — builds the test plan (test cases + edge cases + coverage)
  SRE     — handles production readiness (deploy + monitor + rollback)
  EM      — orchestrates all four via CugaSupervisor, then signs off

Each agent has:
  • 3 skills loaded via CugaSkillsPlugin (Markdown → system prompt)
  • 2-3 tools registered via a dedicated ToolPlugin

Usage
-----
  uv run python main.py
  uv run python main.py --provider anthropic
  uv run python main.py --provider litellm --model GCP/gemini-2.0-flash
  uv run python main.py --feature "Add JWT token refresh endpoint"
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
CUGAPP_ROOT = Path.home() / "Desktop/cuga++"

for _p in [
    CUGAPP_ROOT / "packages/cuga-plugin-sdk/src",
    CUGAPP_ROOT / "packages/cuga-skills/src",
    CUGAPP_ROOT / "packages/cuga-runtime/src",
]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SKILLS_DIR = EXAMPLE_DIR / "skills"


# ---------------------------------------------------------------------------
# Tool plugins — one per agent
# ---------------------------------------------------------------------------

class PMToolPlugin:
    name = "pm-tools"
    version = "1.0.0"

    def on_prompt_build(self, context):
        return None

    def on_tools_build(self, context):
        from cuga_plugin_sdk import ToolContribution
        from agent_tools import create_user_story, define_acceptance_criteria, estimate_story_points
        return ToolContribution(tools=[create_user_story, define_acceptance_criteria, estimate_story_points])


class DevToolPlugin:
    name = "dev-tools"
    version = "1.0.0"

    def on_prompt_build(self, context):
        return None

    def on_tools_build(self, context):
        from cuga_plugin_sdk import ToolContribution
        from agent_tools import design_api_endpoint, suggest_implementation_approach, check_existing_patterns
        return ToolContribution(tools=[design_api_endpoint, suggest_implementation_approach, check_existing_patterns])


class QAToolPlugin:
    name = "qa-tools"
    version = "1.0.0"

    def on_prompt_build(self, context):
        return None

    def on_tools_build(self, context):
        from cuga_plugin_sdk import ToolContribution
        from agent_tools import generate_test_plan, identify_edge_cases, assess_test_coverage
        return ToolContribution(tools=[generate_test_plan, identify_edge_cases, assess_test_coverage])


class SREToolPlugin:
    name = "sre-tools"
    version = "1.0.0"

    def on_prompt_build(self, context):
        return None

    def on_tools_build(self, context):
        from cuga_plugin_sdk import ToolContribution
        from agent_tools import generate_deployment_checklist, suggest_monitoring, classify_rollback_complexity
        return ToolContribution(tools=[generate_deployment_checklist, suggest_monitoring, classify_rollback_complexity])


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _skills_plugin(role: str):
    from cuga_skills import CugaSkillsPlugin
    return CugaSkillsPlugin(skills_dir=str(SKILLS_DIR / role))


def build_team(llm) -> dict:
    """Build all four specialist agents. Returns {name: CugaAgent}."""
    from cuga.sdk import CugaAgent

    pm = CugaAgent(
        special_instructions="You are the PM. Write a user story, acceptance criteria, and story-point estimate.",
        model=llm,
        plugins=[_skills_plugin("pm"), PMToolPlugin()],
        auto_load_policies=False,
    )

    dev = CugaAgent(
        special_instructions="You are the Senior Dev. Design the API endpoint, suggest an implementation approach, and check existing patterns.",
        model=llm,
        plugins=[_skills_plugin("dev"), DevToolPlugin()],
        auto_load_policies=False,
    )

    qa = CugaAgent(
        special_instructions="You are the QA Engineer. Generate a test plan, identify edge cases, and assess coverage readiness.",
        model=llm,
        plugins=[_skills_plugin("qa"), QAToolPlugin()],
        auto_load_policies=False,
    )

    sre = CugaAgent(
        special_instructions="You are the SRE. Produce a deployment checklist, monitoring plan, and rollback complexity classification.",
        model=llm,
        plugins=[_skills_plugin("sre"), SREToolPlugin()],
        auto_load_policies=False,
    )

    return {"pm_agent": pm, "dev_agent": dev, "qa_agent": qa, "sre_agent": sre}


# ---------------------------------------------------------------------------
# Supervisor prompt injected via a plugin
# ---------------------------------------------------------------------------

class EMSkillsPlugin:
    """Injects the EM's orchestration skills + persona into the supervisor prompt."""
    name = "em-skills"
    version = "1.0.0"

    def on_prompt_build(self, context):
        from cuga_plugin_sdk import PromptContribution
        from cuga_skills import SkillsManager
        skills_text = SkillsManager.load_from_directory(str(SKILLS_DIR / "em"))
        persona = (
            "You are an experienced Engineering Manager. "
            "Your job is to coordinate the PM, Dev, QA, and SRE agents in sequence, "
            "collect their outputs, resolve any conflicts, and produce a final ship/hold decision "
            "with a clear stakeholder-ready summary.\n\n"
            "Pipeline:\n"
            "1. Ask pm_agent to write the spec.\n"
            "2. Pass the spec to dev_agent to design the implementation.\n"
            "3. Pass spec + implementation to qa_agent to build the test plan.\n"
            "4. Pass all context to sre_agent to produce the deployment plan.\n"
            "5. Synthesise everything into a final decision with risk score and stakeholder update.\n"
        )
        return PromptContribution(content=persona + "\n\n" + skills_text)

    def on_tools_build(self, context):
        return None


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

DEMO_FEATURE = "Add rate limiting to the public REST API"


async def run(provider: str, model: str | None, feature: str, **kwargs):
    from cuga.sdk import CugaAgent, CugaSupervisor
    from cuga_runtime.llm import create_llm

    banner("Engineering Team Multi-Agent Demo", f"provider={provider}  model={model or 'default'}")

    llm = create_llm(provider=provider, model=model, **kwargs)
    print(f"  Model : {llm.__class__.__name__}\n")

    # Build specialist agents
    agents = build_team(llm)

    # Build supervisor (EM)
    supervisor = CugaSupervisor(
        agents=agents,
        model=llm,
        description="Engineering Manager supervising PM, Dev, QA, and SRE agents",
    )

    print(f"  Feature request: {feature}\n")
    print("  Running pipeline: PM → Dev → QA → SRE → EM sign-off\n")
    print("─" * 62)

    result = await supervisor.invoke(
        f"Process this feature request through the full engineering pipeline:\n\n{feature}",
        thread_id="eng-team-demo",
    )

    print("\n" + "═" * 62)
    print("  FINAL DECISION (Engineering Manager)")
    print("═" * 62)
    for line in str(result).splitlines():
        print(f"  {line}")
    print("═" * 62 + "\n")


def banner(title: str, subtitle: str = ""):
    w = 62
    print(f"\n{'═' * w}")
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print(f"{'═' * w}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Engineering Team multi-agent demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              uv run python main.py
              uv run python main.py --provider anthropic
              uv run python main.py --feature "Add WebSocket support"
              uv run python main.py --provider litellm --model GCP/gemini-2.0-flash
        """),
    )
    parser.add_argument("--provider", "-p", default="rits",
                        choices=["openai", "anthropic", "litellm", "rits", "watsonx", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--feature", "-f", default=DEMO_FEATURE,
                        help="Feature request to process (default: rate limiting)")
    parser.add_argument("--api-base", default=None)
    args = parser.parse_args()

    kwargs = {}
    if args.api_base:
        kwargs["api_base"] = args.api_base
        kwargs["ollama_base_url"] = args.api_base

    try:
        asyncio.run(run(provider=args.provider, model=args.model, feature=args.feature, **kwargs))
    except (ValueError, ImportError) as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
