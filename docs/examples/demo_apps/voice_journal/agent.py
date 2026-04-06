"""
Voice Journal agent tools.

OpenClaw model: one agent, all capabilities as tools.
  save_journal_entry  — write a structured entry to Markdown + SQLite
  list_entries        — query entries by date or recency
  list_dates          — show which dates have entries
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_DIR      = Path(__file__).parent
_DEMOS    = _DIR.parent
_SKILLS   = _DIR / "skills"

for _p in [str(_DIR), str(_DEMOS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_tools():
    from langchain_core.tools import tool
    from store import list_dates as _list_dates
    from store import list_entries as _list_entries
    from store import save_entry

    @tool
    def save_journal_entry(
        body: str,
        title: str = "",
        tags: str = "",
        source: str = "text",
        entry_date: str | None = None,
    ) -> str:
        """
        Save a journal entry to the local Markdown journal and database.

        Args:
            body:       The full journal entry text (cleaned, structured prose).
            title:      Short title (3-7 words). Extract from content if not given.
            tags:       Comma-separated keywords (mood, topic, people, place).
            source:     "text" | "voice" | "upload". Use "voice" for audio transcripts.
            entry_date: ISO date (YYYY-MM-DD). Defaults to today.
        """
        entry = save_entry(body=body, title=title, tags=tags, source=source, entry_date=entry_date)
        return json.dumps(entry)

    @tool
    def list_entries(
        entry_date: str | None = None,
        since_date: str | None = None,
        until_date: str | None = None,
        limit: int = 10,
    ) -> str:
        """
        Return journal entries as a JSON array.

        Args:
            entry_date: Filter to a specific date (YYYY-MM-DD). None = recent entries.
            since_date: Return entries on or after this date (YYYY-MM-DD). Use for "last week", "this month", etc.
            until_date: Return entries on or before this date (YYYY-MM-DD).
            limit:      Maximum number of entries to return (default 10).

        Examples:
            list_entries(since_date="2026-03-29")          # last 7 days
            list_entries(since_date="2026-04-01", until_date="2026-04-05")  # a range
            list_entries(entry_date="2026-04-03")          # a specific day
        """
        return json.dumps(_list_entries(
            entry_date=entry_date,
            since_date=since_date,
            until_date=until_date,
            limit=limit,
        ))

    @tool
    def list_dates() -> str:
        """Return all dates that have journal entries, most recent first."""
        return json.dumps(_list_dates())

    return [save_journal_entry, list_entries, list_dates]


def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=_make_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS))],
        cuga_folder=str(_DIR / ".cuga"),
    )
