"""
CugaHost pipeline factory for the server monitor morning briefing.

Loaded by CugaHost via cuga_pipelines.yaml.
Kept minimal — agent construction is delegated to agent.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def make_briefing_agent():
    """Called by RuntimeFactory with no arguments — matches cuga_pipelines.yaml agent_fn."""
    from agent import make_agent
    return make_agent()
