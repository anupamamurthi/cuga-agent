"""
CUGA Watch Tools — expose watch management as CugaAgent @tool functions.

Level 1: The agent can start, stop, and list background monitors via tool calls.
         The user can say "Watch the Chappaqua Moms group for nanny posts and email
         me" and the agent will plan, parse, and launch the watcher itself.

Level 2: Matches feed back into the same CugaAgent on a stable thread_id, giving
         the agent persistent memory across events so it can reason across matches
         (e.g. "this is the 3rd nanny post this week").
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from langchain_core.tools import tool
from loguru import logger


# ---------------------------------------------------------------------------
# WatchManager — registry of running watch tasks
# ---------------------------------------------------------------------------

class WatchManager:
    """
    Singleton registry of running WatchExecutor background tasks.

    Tracks watch_id → {description, asyncio.Task, thread_id, status}.
    """

    _instance: WatchManager | None = None

    def __new__(cls) -> WatchManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._watches: dict[str, dict[str, Any]] = {}
        return cls._instance

    def register(self, watch_id: str, description: str, task: asyncio.Task, thread_id: str) -> None:
        self._watches[watch_id] = {
            "id": watch_id,
            "description": description,
            "thread_id": thread_id,
            "task": task,
            "status": "running",
        }

    def cancel(self, watch_id: str) -> bool:
        entry = self._watches.get(watch_id)
        if not entry:
            return False
        entry["task"].cancel()
        entry["status"] = "stopped"
        return True

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "id": w["id"],
                "description": w["description"],
                "thread_id": w["thread_id"],
                "status": "running" if not w["task"].done() else "done",
            }
            for w in self._watches.values()
        ]


# Module-level singleton
_manager = WatchManager()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
async def start_watch(config_json: str, thread_id: str = "") -> str:
    """
    Start a background event monitor from a WatchConfig JSON string.

    The monitor polls sources on a schedule, filters by condition, and dispatches
    actions (email, sms, log, agent_notify) when keyword matches are found.

    When thread_id is supplied, agent_notify actions feed matches back into the
    CugaAgent on that thread, giving the agent persistent memory across events.

    Args:
        config_json: JSON string conforming to the WatchConfig schema. Must include
                     sources (type, url, interval_minutes), condition (type, keywords),
                     and actions (type: email|sms|log|agent_notify).
        thread_id:   Optional stable conversation thread ID. If omitted, a new ID
                     is generated. Reuse the same thread_id across restarts to
                     preserve the agent's event memory.

    Returns:
        A summary including the watch_id (use with stop_watch) and thread_id.
    """
    from cuga.watch.models import WatchConfig
    from cuga.watch.executor import WatchExecutor

    try:
        raw = json.loads(config_json)
        config = WatchConfig(**raw)
    except Exception as e:
        return f"Error: could not parse WatchConfig — {e}"

    watch_id = str(uuid.uuid4())[:8]
    effective_thread_id = thread_id or f"watch-{watch_id}"

    executor = WatchExecutor(config, thread_id=effective_thread_id)
    task = asyncio.create_task(
        executor.run(),
        name=f"watch-{watch_id}",
    )
    _manager.register(watch_id, config.description, task, effective_thread_id)

    logger.info(
        f"[watch_tools] Started watch {watch_id!r}: {config.description!r} "
        f"(thread: {effective_thread_id!r})"
    )
    return (
        f"Watch started.\n"
        f"  watch_id:  {watch_id}\n"
        f"  thread_id: {effective_thread_id}\n"
        f"  sources:   {[s.name or s.url for s in config.sources]}\n"
        f"  condition: {config.condition.type} {config.condition.keywords or ''}\n"
        f"  actions:   {[a.type for a in config.actions]}"
    )


@tool
def stop_watch(watch_id: str) -> str:
    """
    Stop a running background event monitor.

    Args:
        watch_id: The ID returned by start_watch.

    Returns:
        Confirmation message.
    """
    if _manager.cancel(watch_id):
        return f"Watch {watch_id!r} stopped."
    return f"No running watch found with id {watch_id!r}. Use list_watches to see active monitors."


@tool
def list_watches() -> str:
    """
    List all background event monitors and their current status.

    Returns:
        A formatted summary of all watches including watch_id, thread_id, and status.
    """
    entries = _manager.status()
    if not entries:
        return "No watches registered."
    lines = ["Active watches:"]
    for w in entries:
        lines.append(
            f"  [{w['status']}] {w['id']} "
            f"(thread: {w['thread_id']}) — {w['description']}"
        )
    return "\n".join(lines)


# Convenience list — pass directly to CugaAgent(tools=watch_tools)
watch_tools = [start_watch, stop_watch, list_watches]
