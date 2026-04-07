"""
Newsletter agent factory.

Called by cuga_pipelines.yaml with no arguments — LLM config comes from
LLM_PROVIDER / LLM_MODEL env vars (same as every other cuga app).

Tools available to the agent:
  search_documents  — semantic search over ChromaDB podcast transcripts
                      (indexed by PodcastChannel after each transcription)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
_SKILLS_DIR  = _EXAMPLE_DIR / "skills"
_CHROMA_DIR  = str(_EXAMPLE_DIR / ".cuga" / "chroma")

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def make_agent():
    from cuga import CugaAgent
    from cuga_channels import make_rag_tools
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=make_rag_tools(
            collection_name="podcasts",
            persist_dir=_CHROMA_DIR,
        ),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )
