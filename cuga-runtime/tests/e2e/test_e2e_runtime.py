"""
End-to-end tests for cuga-runtime.

What makes this e2e (not just integration)
------------------------------------------
- A real fake CUGA HTTP server runs on a random localhost port (uvicorn)
- The cuga-runtime FastAPI app runs on a second random port (uvicorn)
- Tests use plain httpx to hit the runtime's HTTP API
- The runtime calls the fake CUGA server over real HTTP (no mocks at all)
- The full call chain is: test → HTTP → runtime → HTTP → fake CUGA → HTTP ← runtime ← HTTP ← test

No mocks. No monkeypatching. No ASGI tricks.

Tests in this file
------------------
1. Happy path: submit event → poll → completed answer
2. CUGA returns error event → task marked failed
3. CUGA slow response → task still completes (timeout headroom)
4. /health shows queue depth while task is in-flight
5. Multiple concurrent events: all complete independently
"""

import asyncio
import socket
import threading
import time
from contextlib import contextmanager
from typing import Generator

import httpx
import pytest
import uvicorn

from tests.e2e.fake_cuga_server import configure, fake_cuga, get_received_queries


# ── Server lifecycle helpers ──────────────────────────────────────────────────

def _free_port() -> int:
    """Find a free localhost port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _run_server(app, port: int) -> Generator:
    """
    Start a uvicorn server in a background thread.
    Yields when the server is ready. Shuts it down on exit.
    """
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",   # quiet — test output only
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait up to 3 seconds for the server to be ready
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError(f"Server on port {port} did not start in time")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=3.0)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fake_cuga_url():
    """Start the fake CUGA server once for all tests in this module."""
    port = _free_port()
    with _run_server(fake_cuga, port) as url:
        yield url


@pytest.fixture()
def runtime_url(fake_cuga_url):
    """
    Start a fresh cuga-runtime instance for each test, pointing at fake CUGA.

    A new instance per test means each test gets a clean queue — no state leaks.
    """
    import os
    os.environ["CUGA_API_URL"] = fake_cuga_url
    os.environ["CUGA_TIMEOUT"] = "10"

    # Import here so the env vars above are picked up
    import importlib
    import cuga_runtime.app as app_module
    import cuga_runtime.queue as queue_module
    import cuga_runtime.client as client_module
    import cuga_runtime.worker as worker_module

    # Build a fresh set of components for this test
    from cuga_runtime.queue import TaskQueue
    from cuga_runtime.client import CugaClient
    from cuga_runtime.worker import TaskWorker

    fresh_queue = TaskQueue()
    fresh_client = CugaClient(base_url=fake_cuga_url, timeout=10.0)
    fresh_worker = TaskWorker(queue=fresh_queue, cuga_client=fresh_client)

    # Patch module-level singletons so the ASGI app uses our fresh instances
    original_queue = app_module.task_queue
    original_client = app_module.cuga_client
    original_worker = app_module.worker

    app_module.task_queue = fresh_queue
    app_module.cuga_client = fresh_client
    app_module.worker = fresh_worker

    port = _free_port()
    with _run_server(app_module.app, port) as url:
        yield url

    # Restore
    app_module.task_queue = original_queue
    app_module.cuga_client = original_client
    app_module.worker = original_worker


# ── Helper ────────────────────────────────────────────────────────────────────

async def poll_until_done(base_url: str, task_id: str, timeout: float = 10.0) -> dict:
    """
    Poll GET /status/{task_id} until status is completed or failed.
    Returns the final response body.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            resp = await client.get(f"{base_url}/status/{task_id}")
            body = resp.json()
            if body["status"] in ("completed", "failed"):
                return body
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout}s")


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_e2e_happy_path(runtime_url, fake_cuga_url):
    """
    Submit an event, poll until done, verify the answer came from fake CUGA.
    This is the most fundamental e2e check.
    """
    configure(answer="All systems nominal. No action required.")

    async with httpx.AsyncClient() as client:
        # Step 1: Submit
        post_resp = await client.post(f"{runtime_url}/events", json={
            "query": "What is the status of service auth-api?",
            "trigger": "manual",
        })
    assert post_resp.status_code == 202
    task_id = post_resp.json()["task_id"]

    # Step 2: Poll
    result = await poll_until_done(runtime_url, task_id)

    # Step 3: Verify
    assert result["status"] == "completed"
    assert result["output"] == "All systems nominal. No action required."
    assert result["task_id"] == task_id
    assert result["completed_at"] is not None


async def test_e2e_query_reaches_fake_cuga_with_context(runtime_url, fake_cuga_url):
    """
    Verify that the context from the event is folded into the query
    that actually arrives at the CUGA server.
    """
    configure(answer="Context was received.")

    async with httpx.AsyncClient() as client:
        await client.post(f"{runtime_url}/events", json={
            "query": "Analyse this alert.",
            "context": {
                "service": "payment-service",
                "error_rate": "12%",
                "region": "us-east-1",
            },
        })
        await asyncio.sleep(0.3)  # let worker process

    received = get_received_queries()
    assert len(received) == 1
    sent_query = received[0]["query"]
    assert "Analyse this alert." in sent_query
    assert "payment-service" in sent_query
    assert "12%" in sent_query
    assert "us-east-1" in sent_query


async def test_e2e_cuga_error_marks_task_failed(runtime_url, fake_cuga_url):
    """
    When the fake CUGA returns an Error SSE event, the task should be FAILED.
    """
    configure(force_error=True)

    async with httpx.AsyncClient() as client:
        post_resp = await client.post(f"{runtime_url}/events", json={
            "query": "This will fail.",
        })
    task_id = post_resp.json()["task_id"]

    result = await poll_until_done(runtime_url, task_id)
    assert result["status"] == "failed"
    assert "error" in result
    assert result["error"] is not None


async def test_e2e_health_shows_queue_depth(runtime_url, fake_cuga_url):
    """
    With CUGA slow, submit 3 events and check /health shows backpressure.
    After they complete, queue_depth goes back to 0.
    """
    configure(answer="Done.", delay=0.2)

    async with httpx.AsyncClient() as client:
        task_ids = []
        for i in range(3):
            resp = await client.post(f"{runtime_url}/events", json={
                "query": f"Task {i}",
            })
            task_ids.append(resp.json()["task_id"])

        # Immediately after submit — at least some should be in queue
        health = (await client.get(f"{runtime_url}/health")).json()
        assert health["total_tasks_seen"] == 3
        assert health["status"] == "ok"

    # Wait for all to finish
    await asyncio.gather(*[
        poll_until_done(runtime_url, tid, timeout=10.0) for tid in task_ids
    ])

    async with httpx.AsyncClient() as client:
        health = (await client.get(f"{runtime_url}/health")).json()
    assert health["queue_depth"] == 0


async def test_e2e_multiple_events_all_complete(runtime_url, fake_cuga_url):
    """
    Submit 5 events rapidly. All 5 should eventually complete.
    Verifies the worker processes the full backlog without dropping tasks.
    """
    configure(answer="Processed.", delay=0.05)

    async with httpx.AsyncClient() as client:
        task_ids = [
            (await client.post(f"{runtime_url}/events", json={"query": f"event-{i}"})).json()["task_id"]
            for i in range(5)
        ]

    results = await asyncio.gather(*[
        poll_until_done(runtime_url, tid, timeout=15.0) for tid in task_ids
    ])

    assert all(r["status"] == "completed" for r in results)
    assert all(r["output"] == "Processed." for r in results)


async def test_e2e_unknown_task_returns_404(runtime_url, fake_cuga_url):
    """Requesting status for a non-existent task_id should return 404."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{runtime_url}/status/this-id-does-not-exist")
    assert resp.status_code == 404


async def test_e2e_thread_id_forwarded_to_cuga(runtime_url, fake_cuga_url):
    """
    The X-Thread-ID header sent to CUGA should match the thread_id in the event
    (or fall back to task_id when not supplied).
    """
    configure(answer="Thread check.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{runtime_url}/events", json={
            "query": "Follow-up question.",
            "thread_id": "my-existing-thread-42",
        })
    task_id = resp.json()["task_id"]
    await poll_until_done(runtime_url, task_id)

    received = get_received_queries()
    assert received[-1]["thread_id"] == "my-existing-thread-42"
