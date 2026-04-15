"""
Tests for the FastAPI endpoints (/events, /status, /health).

Uses httpx.AsyncClient with the ASGI transport so no server process is needed.
The TaskWorker is NOT started — we test the HTTP layer in isolation and
inspect the queue/result store directly.
"""

import pytest
import httpx
from unittest.mock import patch, AsyncMock

from cuga_runtime.app import app, task_queue
from cuga_runtime.schemas import CugaResultEvent, TaskStatus


@pytest.fixture(autouse=True)
def reset_queue():
    """Clear the shared queue state between tests."""
    task_queue._queue = __import__("asyncio").Queue()
    task_queue._results = {}
    yield


# ── /health ──────────────────────────────────────────────────────────────────

async def test_health_returns_ok():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "queue_depth" in body
    assert "cuga_url" in body


# ── POST /events ──────────────────────────────────────────────────────────────

async def test_post_events_returns_202_and_task_id():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/events", json={"query": "Who is at risk?"})

    assert resp.status_code == 202
    body = resp.json()
    assert "task_id" in body
    assert body["status"] == "queued"


async def test_post_events_enqueues_task():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/events", json={
            "query": "Summarise top deals",
            "persona": "sales_assistant",
            "trigger": "crm.opportunity_updated",
        })

    task_id = resp.json()["task_id"]
    result = task_queue.get_result(task_id)
    assert result is not None
    assert result.status == TaskStatus.QUEUED


async def test_post_events_with_full_payload():
    payload = {
        "query": "Qualify this lead and suggest next steps.",
        "persona": "sales_assistant",
        "trigger": "crm.lead_created",
        "context": {"company": "Acme Corp", "deal_size": "$50k"},
        "callback_url": "https://example.com/webhook",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/events", json=payload)

    assert resp.status_code == 202


async def test_post_events_missing_query_returns_422():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/events", json={"persona": "sales"})

    assert resp.status_code == 422


# ── GET /status/{task_id} ─────────────────────────────────────────────────────

async def test_status_returns_queued_immediately_after_submit():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        post_resp = await client.post("/events", json={"query": "hello"})
        task_id = post_resp.json()["task_id"]
        status_resp = await client.get(f"/status/{task_id}")

    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "queued"


async def test_status_reflects_completed_after_worker_writes_result():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        post_resp = await client.post("/events", json={"query": "hello"})
        task_id = post_resp.json()["task_id"]

        # Simulate what the worker would write
        task_queue.set_result(CugaResultEvent(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            output="The answer is 42.",
            completed_at="2026-04-14T12:00:00",
        ))

        status_resp = await client.get(f"/status/{task_id}")

    body = status_resp.json()
    assert body["status"] == "completed"
    assert body["output"] == "The answer is 42."


async def test_status_unknown_task_returns_404():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/status/does-not-exist")

    assert resp.status_code == 404
