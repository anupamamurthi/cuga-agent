"""
Newsletter curation agent.

This agent receives buffered RSS items (injected by CugaRuntime) and
produces a styled HTML newsletter using the newsletter_curation skill.

It is the CURATION agent — it never manages pipelines or monitors.
Pipeline management is handled by PipelineBuilder + CugaHost.

LLM config comes from LLM_PROVIDER / LLM_MODEL env vars.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE      = Path(__file__).parent
_DEMOS_DIR = _HERE.parent
_SKILLS_DIR = _HERE / "skills"

for _p in [str(_HERE), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_HERE / ".cuga"),
    )
