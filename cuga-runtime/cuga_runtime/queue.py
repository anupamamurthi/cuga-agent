"""
In-memory task queue and result store.

Phase 1 keeps everything in asyncio.Queue + a plain dict.
Swapping this for a DB-backed queue (Phase 2) means replacing
this module only — the worker and API layer don't change.
"""

import asyncio
from typing import Optional

from cuga_runtime.schemas import CugaTaskEvent, CugaResultEvent, TaskStatus


class TaskQueue:
    """
    Holds pending tasks (asyncio.Queue) and completed/failed results (dict).

    The dict is keyed by task_id so the /status endpoint can look up any task.
    Note: In-memory only. Results are lost on process restart (Phase 1 trade-off).
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[CugaTaskEvent] = asyncio.Queue()
        # Stores the latest CugaResultEvent for every task we've seen
        self._results: dict[str, CugaResultEvent] = {}
        # Stores the original CugaTaskEvent for every task (for /stats listing)
        self._events: dict[str, CugaTaskEvent] = {}

    # ── Enqueue ──────────────────────────────────────────────────────────────

    async def put(self, event: CugaTaskEvent) -> None:
        """Accept a new task and mark it as QUEUED immediately."""
        self._events[event.task_id] = event
        self._results[event.task_id] = CugaResultEvent(
            task_id=event.task_id,
            status=TaskStatus.QUEUED,
        )
        await self._queue.put(event)

    # ── Worker-facing ────────────────────────────────────────────────────────

    async def get(self) -> CugaTaskEvent:
        """Block until a task is available and return it."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Signal that the last get()-ed item was processed."""
        self._queue.task_done()

    # ── Result management ────────────────────────────────────────────────────

    def set_result(self, result: CugaResultEvent) -> None:
        """Overwrite the stored result for a task (called by the worker)."""
        self._results[result.task_id] = result

    def get_result(self, task_id: str) -> Optional[CugaResultEvent]:
        """Return the latest result for a task, or None if unknown."""
        return self._results.get(task_id)

    def get_event(self, task_id: str) -> Optional[CugaTaskEvent]:
        """Return the original task event, or None if unknown."""
        return self._events.get(task_id)

    # ── Diagnostics ─────────────────────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def total_tasks(self) -> int:
        return len(self._results)

    def counts_by_status(self) -> dict[str, int]:
        """Return a count of tasks in each status."""
        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for result in self._results.values():
            counts[result.status.value] += 1
        return counts

    def all_results(self) -> list[CugaResultEvent]:
        """Return all results, newest first (by completed_at, falling back to task_id order)."""
        return sorted(
            self._results.values(),
            key=lambda r: r.completed_at or "",
            reverse=True,
        )
