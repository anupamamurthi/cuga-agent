"""
Tests for TaskWorker.

Uses a fake CugaClient so no real CUGA server is needed.
Checks: successful task processing, failure handling, context folding,
and callback delivery.
"""

import asyncio
import pytest

from unittest.mock import AsyncMock, patch

from cuga_runtime.client import CugaClient, CugaClientError
from cuga_runtime.queue import TaskQueue
from cuga_runtime.schemas import CugaTaskEvent, TaskStatus
from cuga_runtime.worker import TaskWorker, _build_query


# ── Helper ───────────────────────────────────────────────────────────────────

def make_worker(cuga_answer: str = "Great answer."):
    """Return a (queue, worker) pair with a mocked CugaClient."""
    queue = TaskQueue()
    client = AsyncMock(spec=CugaClient)
    client.invoke = AsyncMock(return_value=cuga_answer)
    worker = TaskWorker(queue=queue, cuga_client=client)
    return queue, worker, client


async def run_one_task(queue: TaskQueue, worker: TaskWorker, event: CugaTaskEvent):
    """Put one event and let the worker process it, then stop."""
    await queue.put(event)
    # Run the worker loop briefly — it processes the task then we stop it
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)   # give the event loop a tick to process
    worker.stop()
    await asyncio.wait_for(worker_task, timeout=2.0)


# ── Tests ────────────────────────────────────────────────────────────────────

async def test_successful_task_becomes_completed():
    queue, worker, _ = make_worker(cuga_answer="Lead qualified: high priority.")
    event = CugaTaskEvent(query="Qualify this lead", persona="sales")

    await run_one_task(queue, worker, event)

    result = queue.get_result(event.task_id)
    assert result.status == TaskStatus.COMPLETED
    assert "high priority" in result.output
    assert result.completed_at is not None


async def test_cuga_error_marks_task_failed():
    queue = TaskQueue()
    client = AsyncMock(spec=CugaClient)
    client.invoke = AsyncMock(side_effect=CugaClientError("CUGA returned HTTP 503"))
    worker = TaskWorker(queue=queue, cuga_client=client)

    event = CugaTaskEvent(query="Run this query")
    await run_one_task(queue, worker, event)

    result = queue.get_result(event.task_id)
    assert result.status == TaskStatus.FAILED
    assert "503" in result.error


async def test_worker_passes_thread_id_from_event():
    queue, worker, client = make_worker()
    event = CugaTaskEvent(query="Follow up", thread_id="existing-thread-99")

    await run_one_task(queue, worker, event)

    client.invoke.assert_awaited_once_with(
        query="Follow up", thread_id="existing-thread-99"
    )


async def test_worker_uses_task_id_as_thread_id_when_not_set():
    queue, worker, client = make_worker()
    event = CugaTaskEvent(query="New task")  # no thread_id

    await run_one_task(queue, worker, event)

    _, kwargs = client.invoke.call_args
    assert kwargs["thread_id"] == event.task_id


async def test_callback_url_is_called_on_completion():
    queue, worker, _ = make_worker(cuga_answer="Done.")
    event = CugaTaskEvent(
        query="Analyse ticket",
        callback_url="http://my-app/webhook",
    )

    with patch("cuga_runtime.worker._send_callback", new_callable=AsyncMock) as mock_cb:
        await run_one_task(queue, worker, event)
        mock_cb.assert_awaited_once()
        call_args = mock_cb.call_args[0]
        assert call_args[0] == "http://my-app/webhook"
        assert call_args[1].status == TaskStatus.COMPLETED


async def test_status_transitions_queued_then_running_then_completed():
    """The status should progress through the expected lifecycle."""
    queue = TaskQueue()
    client = AsyncMock(spec=CugaClient)

    # Capture when the worker marks RUNNING vs COMPLETED
    statuses_seen = []

    original_set_result = queue.set_result

    def recording_set_result(result):
        statuses_seen.append(result.status)
        original_set_result(result)

    queue.set_result = recording_set_result

    client.invoke = AsyncMock(return_value="Answer here.")
    worker = TaskWorker(queue=queue, cuga_client=client)

    event = CugaTaskEvent(query="Test lifecycle")
    await run_one_task(queue, worker, event)

    assert TaskStatus.RUNNING in statuses_seen
    assert TaskStatus.COMPLETED in statuses_seen
    # QUEUED is set by queue.put(), not set_result, so it won't be in this list
    final = queue.get_result(event.task_id)
    assert final.status == TaskStatus.COMPLETED


# ── _build_query unit tests ───────────────────────────────────────────────────

def test_build_query_no_context():
    event = CugaTaskEvent(query="Simple question")
    assert _build_query(event) == "Simple question"


def test_build_query_with_context():
    event = CugaTaskEvent(
        query="Qualify this lead",
        context={"company": "Acme", "deal_size": "$50k"},
    )
    result = _build_query(event)
    assert result.startswith("Qualify this lead")
    assert "Context:" in result
    assert "company: Acme" in result
    assert "deal_size: $50k" in result
