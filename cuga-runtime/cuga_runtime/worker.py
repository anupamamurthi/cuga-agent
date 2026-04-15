"""
Background worker: pulls tasks from the queue and invokes CUGA.

One worker processes tasks one at a time (sequential by default).
For higher throughput, run multiple worker coroutines against the same queue —
they each pull independently and asyncio handles the concurrency.

The worker:
  1. Pulls an event from the queue
  2. Marks it RUNNING in the result store
  3. Builds the full query (base query + any context from the event)
  4. Calls CUGA via CugaClient
  5. Writes the result (COMPLETED or FAILED) back to the result store
  6. POSTs to callback_url if provided
"""

import asyncio
import datetime
import logging

import httpx

from cuga_runtime.client import CugaClient, CugaClientError
from cuga_runtime.queue import TaskQueue
from cuga_runtime.schemas import CugaResultEvent, CugaTaskEvent, TaskStatus

logger = logging.getLogger(__name__)


def _build_query(event: CugaTaskEvent) -> str:
    """
    Combine the event's base query with any structured context.

    If context is provided, it's appended as a readable block so the agent
    has the full picture without the caller needing to format it themselves.

    Example:
        query   = "Qualify this lead and suggest next steps."
        context = {"name": "Acme Corp", "deal_size": "$50k", "source": "LinkedIn"}

    Result:
        "Qualify this lead and suggest next steps.\n\nContext:\n  name: Acme Corp\n  ..."
    """
    if not event.context:
        return event.query

    context_lines = "\n".join(f"  {k}: {v}" for k, v in event.context.items())
    return f"{event.query}\n\nContext:\n{context_lines}"


async def _send_callback(url: str, result: CugaResultEvent) -> None:
    """Fire-and-forget POST to the caller's callback URL."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(url, json=result.model_dump())
    except Exception as exc:
        logger.warning("Callback to %s failed: %s", url, exc)


class TaskWorker:
    """
    Runs a continuous loop pulling tasks from the queue and calling CUGA.

    Instantiate it, then call `await worker.run()` in a background task
    (typically started in FastAPI's lifespan handler).
    """

    def __init__(self, queue: TaskQueue, cuga_client: CugaClient) -> None:
        self.queue = queue
        self.cuga_client = cuga_client
        self._running = False

    async def run(self) -> None:
        """
        Main loop. Runs until `stop()` is called.
        Processes one task at a time; errors on individual tasks are caught
        so the loop never crashes.
        """
        self._running = True
        logger.info("TaskWorker started")

        while self._running:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # no tasks right now, loop back

            try:
                await self._process(event)
            except Exception as exc:
                # Unexpected error outside normal CUGA failure path
                logger.exception("Unexpected error processing task %s", event.task_id)
                self.queue.set_result(
                    CugaResultEvent(
                        task_id=event.task_id,
                        status=TaskStatus.FAILED,
                        error=f"Internal worker error: {exc}",
                        completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    )
                )
            finally:
                self.queue.task_done()

        logger.info("TaskWorker stopped")

    async def _process(self, event: CugaTaskEvent) -> None:
        """Handle a single task end-to-end."""
        logger.info(
            "Processing task %s | trigger=%s | persona=%s",
            event.task_id,
            event.trigger,
            event.persona,
        )

        # Mark running so /status shows progress immediately
        self.queue.set_result(
            CugaResultEvent(task_id=event.task_id, status=TaskStatus.RUNNING)
        )

        thread_id = event.thread_id or event.task_id
        query = _build_query(event)

        try:
            answer = await self.cuga_client.invoke(query=query, thread_id=thread_id)
            result = CugaResultEvent(
                task_id=event.task_id,
                status=TaskStatus.COMPLETED,
                output=answer,
                thread_id=thread_id,
                completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
        except CugaClientError as exc:
            result = CugaResultEvent(
                task_id=event.task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
                thread_id=thread_id,
                completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
            logger.warning("Task %s failed: %s", event.task_id, exc)

        self.queue.set_result(result)
        logger.info("Task %s → %s", event.task_id, result.status)

        if event.callback_url:
            await _send_callback(event.callback_url, result)

    def stop(self) -> None:
        self._running = False
