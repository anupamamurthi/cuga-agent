"""
Voice Journal store — saves entries to dated Markdown files.

Each entry is appended to journal/YYYY-MM-DD.md.
The agent calls save_journal_entry(); list_entries() is used
to query what's been saved.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

_DIR     = Path(__file__).parent
_JOURNAL = _DIR / "journal"
_DB_PATH = _DIR / "journal.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT    NOT NULL,   -- YYYY-MM-DD
    title      TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL,
    tags       TEXT    NOT NULL DEFAULT '',
    source     TEXT    NOT NULL DEFAULT 'text',  -- text | voice | upload
    created_at TEXT    NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    _JOURNAL.mkdir(exist_ok=True)
    with _conn() as con:
        con.execute(_CREATE)


def save_entry(
    body: str,
    title: str = "",
    tags: str = "",
    source: str = "text",
    entry_date: str | None = None,
) -> dict[str, Any]:
    today    = entry_date or date.today().isoformat()
    now_str  = datetime.now().isoformat(timespec="seconds")

    with _conn() as con:
        cur = con.execute(
            """INSERT INTO entries (entry_date, title, body, tags, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (today, title, body, tags, source, now_str),
        )
        entry_id = cur.lastrowid

    # Append to dated Markdown file
    md_path = _JOURNAL / f"{today}.md"
    with open(md_path, "a", encoding="utf-8") as f:
        if md_path.stat().st_size == 0:
            f.write(f"# Journal — {today}\n\n")
        heading = f"## {title}" if title else f"## Entry at {now_str[11:16]}"
        tag_line = f"\n*Tags: {tags}*" if tags else ""
        source_line = f"\n*Source: {source}*" if source != "text" else ""
        f.write(f"{heading}{source_line}{tag_line}\n\n{body}\n\n---\n\n")

    return {"id": entry_id, "date": today, "title": title, "source": source}


def list_entries(
    entry_date: str | None = None,
    since_date: str | None = None,
    until_date: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    with _conn() as con:
        if entry_date:
            rows = con.execute(
                "SELECT * FROM entries WHERE entry_date = ? ORDER BY created_at DESC LIMIT ?",
                (entry_date, limit),
            ).fetchall()
        elif since_date or until_date:
            conditions = []
            params: list[Any] = []
            if since_date:
                conditions.append("entry_date >= ?")
                params.append(since_date)
            if until_date:
                conditions.append("entry_date <= ?")
                params.append(until_date)
            where = " AND ".join(conditions)
            params.append(limit)
            rows = con.execute(
                f"SELECT * FROM entries WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM entries ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def list_dates() -> list[str]:
    """Return all dates that have entries, most recent first."""
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT entry_date FROM entries ORDER BY entry_date DESC"
        ).fetchall()
    return [r["entry_date"] for r in rows]
