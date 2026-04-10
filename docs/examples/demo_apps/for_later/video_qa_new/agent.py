"""
Video Q&A — CugaAgent with video tools.

The agent handles two roles depending on which runtime it's in:

  Direct (Q&A):
    User asks about a video → agent transcribes + indexes → answers with timestamps
    Tools: transcribe_and_index, search_video_segments, get_segment_at_time

  Pipeline (folder watcher):
    AudioChannelEnhanced detects new files → segments buffered → cron fires →
    agent ingests segments, searches for keywords, EmailChannel delivers report
    Tools: ingest_video_segments, search_video_segments

LLM config: LLM_PROVIDER / LLM_MODEL env vars.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE      = Path(__file__).parent
_DEMOS_DIR = _HERE.parent
_SKILLS    = _HERE / "skills"

for _p in [str(_HERE), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CHROMA_DIR        = str(_HERE / ".chroma")
_CHROMA_COLLECTION = "video_qa_segments"


def make_agent():
    from cuga import CugaAgent
    from cuga_channels import make_video_tools
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    tools = make_video_tools(
        collection_name=_CHROMA_COLLECTION,
        persist_dir=_CHROMA_DIR,
    )

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=tools,
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS))],
        cuga_folder=str(_HERE / ".cuga"),
    )
