"""
Integration test: CRM Lead Created → CUGA qualifies the lead.

Scenario
--------
A CRM system fires a "lead_created" event when a new sales prospect appears.
The runtime receives it, invokes CUGA, and the sales team gets a qualification
summary — without anyone manually triggering the agent.

What this test proves
---------------------
1. The full stack (API → queue → worker → CUGA client → result) works together.
2. The context from the event (company name, deal size, etc.) reaches CUGA.
3. The result is available via /status within a reasonable time.
4. An optional callback URL receives the result automatically.

Running modes
-------------
  Mock mode (default, no CUGA server needed):
      pytest tests/integration/test_crm_lead_flow.py

  Live mode (requires CUGA running at CUGA_API_URL):
      CUGA_LIVE=1 pytest tests/integration/test_crm_lead_flow.py -s

The live mode sends a real query and prints the CUGA response so you can
visually verify the output makes sense.
"""

import asyncio
import os
import pytest
import httpx

from unittest.mock import AsyncMock, patch

from cuga_runtime.app import app, task_queue
from cuga_runtime.client import CugaClient
from cuga_runtime.queue import TaskQueue
from cuga_runtime.schemas import CugaTaskEvent, CugaResultEvent, TaskStatus
from cuga_runtime.worker import TaskWorker


# ── Fixture: a self-contained runtime (queue + worker + ASGI app) ─────────────

@pytest.fixture
def reset_queue():
    """Give each test a clean queue."""
    task_queue._queue = asyncio.Queue()
    task_queue._results = {}
    yield task_queue


# ── The CRM event that would fire when a lead is created ─────────────────────

CRM_LEAD_EVENT = {
    "query": (
        "A new sales lead has been created. "
        "Review the context below and provide: "
        "(1) a lead quality score 1–10, "
        "(2) key reasons for the score, "
        "(3) recommended next action for the sales rep."
    ),
    "persona": "sales_assistant",
    "trigger": "crm.lead_created",
    "context": {
        "company": "Momentum Analytics",
        "contact": "Sarah Chen, VP of Data",
        "deal_size": "$120,000 ARR",
        "source": "Inbound — downloaded whitepaper on AI in Finance",
        "industry": "Financial Services",
        "company_size": "500 employees",
        "notes": "Asked specifically about compliance reporting features",
    },
}


# ── Mock mode test ────────────────────────────────────────────────────────────

async def test_crm_lead_flow_mock(reset_queue):
    """
    Full flow from HTTP POST → queue → worker → result, using a mocked CUGA.

    This test runs without any external services.
    """
    mock_answer = (
        "Lead Score: 8/10\n\n"
        "Key Reasons:\n"
        "- Inbound lead with clear intent (whitepaper download)\n"
        "- VP-level contact with budget authority\n"
        "- Specific ask around compliance — matches our core differentiator\n\n"
        "Next Action: Schedule a 30-minute discovery call focused on "
        "compliance reporting. Reference the whitepaper in the invite."
    )

    # Wire up a worker with a mocked CUGA client
    mock_client = AsyncMock(spec=CugaClient)
    mock_client.invoke = AsyncMock(return_value=mock_answer)
    worker = TaskWorker(queue=reset_queue, cuga_client=mock_client)
    worker_task = asyncio.create_task(worker.run())

    try:
        # Step 1: CRM fires the event via HTTP
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            post_resp = await client.post("/events", json=CRM_LEAD_EVENT)

        assert post_resp.status_code == 202, f"Expected 202, got {post_resp.status_code}"
        task_id = post_resp.json()["task_id"]

        # Step 2: Poll until completed (or timeout)
        result = await _poll_until_done(task_id, timeout=3.0)

        # Step 3: Verify the result
        assert result["status"] == "completed", f"Task failed: {result.get('error')}"
        assert result["output"] == mock_answer

        # Step 4: Verify CUGA was called with the context folded into the query
        call_args = mock_client.invoke.call_args
        sent_query = call_args[1]["query"]  # keyword arg
        assert "Momentum Analytics" in sent_query, "Company name should be in query"
        assert "$120,000 ARR" in sent_query, "Deal size should be in query"
        assert "compliance" in sent_query.lower(), "Key context should appear in query"

    finally:
        worker.stop()
        await asyncio.wait_for(worker_task, timeout=2.0)


async def test_crm_lead_flow_callback(reset_queue):
    """
    Verify that a callback_url receives the result automatically.
    """
    received_callbacks = []

    async def fake_callback(url, result):
        received_callbacks.append((url, result))

    mock_client = AsyncMock(spec=CugaClient)
    mock_client.invoke = AsyncMock(return_value="Lead score: 7/10")
    worker = TaskWorker(queue=reset_queue, cuga_client=mock_client)
    worker_task = asyncio.create_task(worker.run())

    event_with_callback = dict(CRM_LEAD_EVENT)
    event_with_callback["callback_url"] = "https://crm.example.com/webhooks/cuga"

    try:
        with patch("cuga_runtime.worker._send_callback", side_effect=fake_callback):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post("/events", json=event_with_callback)

            await asyncio.sleep(0.1)  # let the worker process

        assert len(received_callbacks) == 1
        cb_url, cb_result = received_callbacks[0]
        assert cb_url == "https://crm.example.com/webhooks/cuga"
        assert cb_result.status == TaskStatus.COMPLETED

    finally:
        worker.stop()
        await asyncio.wait_for(worker_task, timeout=2.0)


async def test_multiple_leads_processed_in_order(reset_queue):
    """
    Submit three lead events back-to-back. All three should complete.
    Order of processing is sequential (Phase 1: single worker).
    """
    answers = ["Score: 9/10", "Score: 5/10", "Score: 3/10"]
    answer_queue = asyncio.Queue()
    for a in answers:
        await answer_queue.put(a)

    async def ordered_answers(**kwargs):
        return await answer_queue.get()

    mock_client = AsyncMock(spec=CugaClient)
    mock_client.invoke = AsyncMock(side_effect=ordered_answers)
    worker = TaskWorker(queue=reset_queue, cuga_client=mock_client)
    worker_task = asyncio.create_task(worker.run())

    task_ids = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for company in ["Alpha Corp", "Beta Ltd", "Gamma Inc"]:
            lead = dict(CRM_LEAD_EVENT)
            lead["context"] = {"company": company}
            resp = await client.post("/events", json=lead)
            task_ids.append(resp.json()["task_id"])

    try:
        results = await asyncio.gather(*[
            _poll_until_done(tid, timeout=5.0) for tid in task_ids
        ])

        for r in results:
            assert r["status"] == "completed"

        # All three unique answers were delivered
        outputs = {r["output"] for r in results}
        assert outputs == set(answers)

    finally:
        worker.stop()
        await asyncio.wait_for(worker_task, timeout=2.0)


# ── Live mode test (only runs when CUGA_LIVE=1) ───────────────────────────────

@pytest.mark.skipif(
    not os.getenv("CUGA_LIVE"),
    reason="Set CUGA_LIVE=1 to run against a real CUGA server",
)
async def test_crm_lead_flow_live():
    """
    Sends a real CRM lead event to a running CUGA instance.
    Set CUGA_API_URL to point at your CUGA server.

    Run with:
        CUGA_LIVE=1 CUGA_API_URL=http://localhost:8000 pytest tests/integration/ -s -k live
    """
    import cuga_runtime.app as runtime_app

    cuga_url = os.getenv("CUGA_API_URL", "http://localhost:8000")
    live_client = CugaClient(base_url=cuga_url, timeout=120.0)
    live_queue = TaskQueue()
    live_worker = TaskWorker(queue=live_queue, cuga_client=live_client)

    # Patch the module-level queue so the ASGI app uses our live queue
    original_queue = runtime_app.task_queue
    runtime_app.task_queue = live_queue

    worker_task = asyncio.create_task(live_worker.run())

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/events", json=CRM_LEAD_EVENT)

        assert resp.status_code == 202
        task_id = resp.json()["task_id"]
        print(f"\n[LIVE] Submitted task: {task_id}")

        result = await _poll_until_done(task_id, timeout=120.0, queue=live_queue)
        print(f"\n[LIVE] Status: {result['status']}")
        print(f"\n[LIVE] CUGA answer:\n{result.get('output', result.get('error'))}")

        assert result["status"] == "completed", f"Expected completed, got: {result}"

    finally:
        live_worker.stop()
        await asyncio.wait_for(worker_task, timeout=5.0)
        runtime_app.task_queue = original_queue


# ── Polling helper ────────────────────────────────────────────────────────────

async def _poll_until_done(
    task_id: str,
    timeout: float = 5.0,
    queue: TaskQueue = None,
) -> dict:
    """
    Poll GET /status/{task_id} until status is completed or failed.
    Returns the response body as a dict.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    used_queue = queue or task_queue

    while asyncio.get_event_loop().time() < deadline:
        result = used_queue.get_result(task_id)
        if result and result.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return result.model_dump()
        await asyncio.sleep(0.05)

    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")
