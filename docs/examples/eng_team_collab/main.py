#!/usr/bin/env python3
"""
Engineering Team Collab — Back-and-forth multi-agent demo
===========================================================

Agents route work to each other dynamically via HANDOFF_TO directives.
No fixed pipeline — QA can bounce bugs back to Dev, SRE can escalate to EM, etc.

Usage:
  uv run python main.py
  uv run python main.py --feature "Add OAuth2 support"
  uv run python main.py --provider anthropic
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import textwrap
from pathlib import Path

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

DEMO_FEATURE = "Add rate limiting to the public REST API"

AGENT_LABELS = {
    "pm_agent":  "📋 PM",
    "dev_agent": "💻 Dev",
    "qa_agent":  "🧪 QA",
    "sre_agent": "🔧 SRE",
    "em_agent":  "🎯 EM",
}


# ---------------------------------------------------------------------------
# Tool plugins
# ---------------------------------------------------------------------------

class PMPlugin:
    name = "pm-collab"; version = "1.0.0"
    def on_prompt_build(self, ctx): return None
    def on_tools_build(self, ctx):
        from cuga_plugin_sdk import ToolContribution
        from collab_tools import write_implementation  # PM doesn't need dev tools
        # PM uses no special tools — skills are enough
        return None


class DevPlugin:
    name = "dev-collab"; version = "1.0.0"
    def on_prompt_build(self, ctx): return None
    def on_tools_build(self, ctx):
        from cuga_plugin_sdk import ToolContribution
        from collab_tools import write_implementation, fix_bug, explain_design_decision
        return ToolContribution(tools=[write_implementation, fix_bug, explain_design_decision])


class QAPlugin:
    name = "qa-collab"; version = "1.0.0"
    def on_prompt_build(self, ctx): return None
    def on_tools_build(self, ctx):
        from cuga_plugin_sdk import ToolContribution
        from collab_tools import review_implementation, report_bug, sign_off
        return ToolContribution(tools=[review_implementation, report_bug, sign_off])


class SREPlugin:
    name = "sre-collab"; version = "1.0.0"
    def on_prompt_build(self, ctx): return None
    def on_tools_build(self, ctx):
        from cuga_plugin_sdk import ToolContribution
        from collab_tools import assess_deployment_risk, request_addition, approve_deployment
        return ToolContribution(tools=[assess_deployment_risk, request_addition, approve_deployment])


class EMPlugin:
    name = "em-collab"; version = "1.0.0"
    def on_prompt_build(self, ctx): return None
    def on_tools_build(self, ctx):
        from cuga_plugin_sdk import ToolContribution
        from collab_tools import arbitrate, final_decision
        return ToolContribution(tools=[arbitrate, final_decision])


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

def build_agents(llm) -> dict:
    from cuga.sdk import CugaAgent
    from cuga_skills import CugaSkillsPlugin

    def sp(role): return CugaSkillsPlugin(skills_dir=str(SKILLS_DIR / role))

    return {
        "pm_agent":  CugaAgent(special_instructions="You are the PM in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("pm"),  PMPlugin()],  auto_load_policies=False),
        "dev_agent": CugaAgent(special_instructions="You are the Senior Dev in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("dev"), DevPlugin()], auto_load_policies=False),
        "qa_agent":  CugaAgent(special_instructions="You are the QA Engineer in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("qa"),  QAPlugin()],  auto_load_policies=False),
        "sre_agent": CugaAgent(special_instructions="You are the SRE in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("sre"), SREPlugin()], auto_load_policies=False),
        "em_agent":  CugaAgent(special_instructions="You are the Engineering Manager in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("em"),  EMPlugin()],  auto_load_policies=False),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run(provider: str, model: str | None, feature: str, **kwargs):
    from cuga_runtime.llm import create_llm
    from orchestrator import CollabOrchestrator

    print(f"\n{'═'*64}")
    print(f"  Eng Team Collab — Back-and-forth multi-agent demo")
    print(f"  provider={provider}  model={model or 'default'}")
    print(f"{'═'*64}")
    print(f"\n  Feature: {feature}\n")

    llm = create_llm(provider=provider, model=model, **kwargs)

    agents = build_agents(llm)
    orch   = CollabOrchestrator(agents=agents, max_rounds=12)

    async def on_turn(turn):
        label = AGENT_LABELS.get(turn.agent, turn.agent)
        next_label = AGENT_LABELS.get(turn.handoff_to, turn.handoff_to)
        print(f"\n{'─'*64}")
        print(f"  Round {turn.round}  {label}")
        print(f"{'─'*64}")
        for line in turn.message.splitlines():
            print(f"  {line}")
        if turn.tool_calls:
            print(f"\n  [{len(turn.tool_calls)} tool call(s): "
                  f"{', '.join(tc['name'] for tc in turn.tool_calls)}]")
        if turn.is_complete:
            print(f"\n  → Pipeline complete ✅")
        else:
            print(f"\n  → Handing off to {next_label}")

    conversation = await orch.run(feature=feature, on_turn=on_turn)

    print(f"\n{'═'*64}")
    print(f"  Done — {len(conversation)} rounds, "
          f"{'complete ✅' if conversation[-1].is_complete else 'max rounds reached'}")
    print(f"{'═'*64}\n")


def main():
    parser = argparse.ArgumentParser(description="Eng Team Collab demo")
    parser.add_argument("--provider", "-p", default="rits",
                        choices=["openai", "anthropic", "litellm", "rits", "watsonx", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--feature", "-f", default=DEMO_FEATURE)
    parser.add_argument("--api-base", default=None)
    args = parser.parse_args()

    kwargs = {}
    if args.api_base:
        kwargs["api_base"] = args.api_base
        kwargs["ollama_base_url"] = args.api_base

    asyncio.run(run(provider=args.provider, model=args.model, feature=args.feature, **kwargs))


if __name__ == "__main__":
    main()
