"""
Tests for TaskQueue.

Checks: put/get ordering, result lifecycle (QUEUED → RUNNING → COMPLETED),
and the diagnostic counters.
"""

import pytest
from cuga_runtime.queue import TaskQueue
from cuga_runtime.schemas import CugaTaskEvent, CugaResultEvent, TaskStatus


@pytest.fixture
def queue():
    return TaskQueue()


async def test_put_sets_queued_status(queue):
    event = CugaTaskEvent(query="hello")
    await queue.put(event)

    result = queue.get_result(event.task_id)
    assert result is not None
    assert result.status == TaskStatus.QUEUED


async def test_get_returns_event_in_order(queue):
    e1 = CugaTaskEvent(query="first")
    e2 = CugaTaskEvent(query="second")
    await queue.put(e1)
    await queue.put(e2)

    got_first = await queue.get()
    got_second = await queue.get()

    assert got_first.task_id == e1.task_id
    assert got_second.task_id == e2.task_id


async def test_set_result_overwrites_status(queue):
    event = CugaTaskEvent(query="hello")
    await queue.put(event)

    queue.set_result(CugaResultEvent(
        task_id=event.task_id,
        status=TaskStatus.RUNNING,
    ))
    assert queue.get_result(event.task_id).status == TaskStatus.RUNNING

    queue.set_result(CugaResultEvent(
        task_id=event.task_id,
        status=TaskStatus.COMPLETED,
        output="Done.",
    ))
    result = queue.get_result(event.task_id)
    assert result.status == TaskStatus.COMPLETED
    assert result.output == "Done."


async def test_unknown_task_id_returns_none(queue):
    assert queue.get_result("does-not-exist") is None


async def test_pending_count(queue):
    assert queue.pending_count == 0
    await queue.put(CugaTaskEvent(query="a"))
    await queue.put(CugaTaskEvent(query="b"))
    assert queue.pending_count == 2


async def test_total_tasks(queue):
    assert queue.total_tasks == 0
    await queue.put(CugaTaskEvent(query="one"))
    await queue.put(CugaTaskEvent(query="two"))
    assert queue.total_tasks == 2
