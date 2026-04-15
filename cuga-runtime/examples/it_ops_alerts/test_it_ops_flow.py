"""
Tests for the IT Ops alert triage use case.

Two levels:
  1. Integration test (mock CUGA client) — fast, validates the domain logic
  2. E2e test (real HTTP through fake CUGA server) — validates full HTTP path

The integration tests are the primary test suite.
The e2e test is the showpiece — it demonstrates exactly what happens when
a monitoring system fires four alerts and CUGA triages them all.
"""

import asyncio
import sys
import os

import pytest
import httpx

from cuga_runtime.client import CugaClient
from cuga_runtime.queue import TaskQueue
from cuga_runtime.schemas import CugaTaskEvent, TaskStatus
from cuga_runtime.worker import TaskWorker, _build_query

from examples.it_ops_alerts.sample_alerts import (
    ALL_ALERTS,
    CPU_HIGH,
    DEPLOYMENT_ANOMALY,
    ERROR_RATE_SPIKE,
    LATENCY_SPIKE,
    OPS_TRIAGE_PROMPT,
)

# Add tests/e2e to path so we can import the fake server
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests"))
from e2e.fake_cuga_server import configure, fake_cuga, get_received_queries
from e2e.test_e2e_runtime import _free_port, _run_server, poll_until_done


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_alert_event(alert: dict) -> CugaTaskEvent:
    """Convert a sample alert dict into a CugaTaskEvent."""
    return CugaTaskEvent(
        query=OPS_TRIAGE_PROMPT,
        persona="ops_agent",
        trigger=alert["trigger"],
        context=alert["context"],
    )


async def run_worker_briefly(queue, client, seconds=0.2):
    worker = TaskWorker(queue=queue, cuga_client=client)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(seconds)
    worker.stop()
    await asyncio.wait_for(task, timeout=2.0)
    return worker


# ── Integration tests (mocked CUGA) ──────────────────────────────────────────

class TestAlertEventConstruction:
    """Verify that alert context is correctly packaged into CugaTaskEvents."""

    def test_cpu_alert_event_has_correct_trigger(self):
        event = make_alert_event(CPU_HIGH)
        assert event.trigger == "alert.cpu_high"
        assert event.persona == "ops_agent"

    def test_context_contains_alert_fields(self):
        event = make_alert_event(CPU_HIGH)
        assert event.context["service"] == "auth-api"
        assert event.context["current_value"] == "94%"
        assert "auth-api v2.4.1" in event.context["recent_deployments"]

    def test_query_is_the_triage_prompt(self):
        event = make_alert_event(ERROR_RATE_SPIKE)
        assert "P1/P2/P3" in event.query
        assert "root cause" in event.query.lower()

    def test_build_query_includes_all_alert_fields(self):
        event = make_alert_event(LATENCY_SPIKE)
        full_query = _build_query(event)
        # The prompt is there
        assert "P1/P2/P3" in full_query
        # All context fields are there
        assert "search-api" in full_query
        assert "3400" in full_query
        assert "elasticsearch" in full_query
        assert "re-index" in full_query


class TestAlertProcessing:
    """Verify that the worker correctly processes alert events end-to-end."""

    async def test_cpu_alert_is_processed(self):
        queue = TaskQueue()
        from unittest.mock import AsyncMock
        client = AsyncMock(spec=CugaClient)
        client.invoke = AsyncMock(return_value=(
            "Severity: P1 — CPU at 94% for 8 min, correlated with recent deploy.\n"
            "Root cause: auth-api v2.4.1 memory leak causing GC pressure.\n"
            "Action: Immediate rollback to v2.4.0.\n"
            "Escalate: Yes — notify auth-api team lead."
        ))

        event = make_alert_event(CPU_HIGH)
        await queue.put(event)
        await run_worker_briefly(queue, client)

        result = queue.get_result(event.task_id)
        assert result.status == TaskStatus.COMPLETED
        assert "P1" in result.output
        assert "rollback" in result.output.lower()

    async def test_all_four_alert_types_are_processed(self):
        """Fire all four alert types — all should complete."""
        from unittest.mock import AsyncMock
        queue = TaskQueue()
        client = AsyncMock(spec=CugaClient)
        client.invoke = AsyncMock(return_value="Triage complete.")

        events = [make_alert_event(a) for a in ALL_ALERTS]
        for e in events:
            await queue.put(e)

        await run_worker_briefly(queue, client, seconds=0.5)

        for e in events:
            result = queue.get_result(e.task_id)
            assert result is not None
            assert result.status == TaskStatus.COMPLETED, (
                f"Alert {e.trigger} did not complete: {result}"
            )

    async def test_deployment_anomaly_context_reaches_cuga(self):
        """The deployment alert's rollback info should appear in the query sent to CUGA."""
        from unittest.mock import AsyncMock, call
        queue = TaskQueue()
        client = AsyncMock(spec=CugaClient)
        client.invoke = AsyncMock(return_value="Rollback recommended.")

        event = make_alert_event(DEPLOYMENT_ANOMALY)
        await queue.put(event)
        await run_worker_briefly(queue, client)

        sent_query = client.invoke.call_args[1]["query"]
        assert "recommendation-engine" in sent_query
        assert "v3.0.9" in sent_query       # rollback version
        assert "new_model_serving_enabled" in sent_query  # feature flag

    async def test_failed_cuga_call_does_not_block_next_alert(self):
        """If one alert fails, the worker should keep processing subsequent ones."""
        from unittest.mock import AsyncMock
        from cuga_runtime.client import CugaClientError

        queue = TaskQueue()
        client = AsyncMock(spec=CugaClient)
        results_sequence = [
            CugaClientError("CUGA unavailable"),  # first call fails
            "Triage complete.",                    # second succeeds
        ]
        client.invoke = AsyncMock(side_effect=results_sequence)

        e1 = make_alert_event(CPU_HIGH)
        e2 = make_alert_event(ERROR_RATE_SPIKE)
        await queue.put(e1)
        await queue.put(e2)
        await run_worker_briefly(queue, client, seconds=0.3)

        assert queue.get_result(e1.task_id).status == TaskStatus.FAILED
        assert queue.get_result(e2.task_id).status == TaskStatus.COMPLETED


# ── E2e test: real HTTP ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fake_cuga_url_for_alerts():
    port = _free_port()
    with _run_server(fake_cuga, port) as url:
        yield url


@pytest.fixture()
def runtime_url_for_alerts(fake_cuga_url_for_alerts):
    import os
    os.environ["CUGA_API_URL"] = fake_cuga_url_for_alerts

    import cuga_runtime.app as app_module
    from cuga_runtime.queue import TaskQueue
    from cuga_runtime.client import CugaClient
    from cuga_runtime.worker import TaskWorker

    fresh_queue = TaskQueue()
    fresh_client = CugaClient(base_url=fake_cuga_url_for_alerts, timeout=10.0)
    fresh_worker = TaskWorker(queue=fresh_queue, cuga_client=fresh_client)

    orig_q, orig_c, orig_w = app_module.task_queue, app_module.cuga_client, app_module.worker
    app_module.task_queue = fresh_queue
    app_module.cuga_client = fresh_client
    app_module.worker = fresh_worker

    port = _free_port()
    with _run_server(app_module.app, port) as url:
        yield url

    app_module.task_queue = orig_q
    app_module.cuga_client = orig_c
    app_module.worker = orig_w


async def test_e2e_four_alerts_all_triaged(runtime_url_for_alerts, fake_cuga_url_for_alerts):
    """
    E2E showpiece test.

    Four different alert types fire simultaneously (as a monitoring system would do
    during an incident). All four reach fake CUGA, get triaged, and results are
    available via /status. This is the event-driven pattern in action.

    What this proves:
    - Monitoring system fires → runtime accepts without blocking (202)
    - Runtime worker calls CUGA for each alert asynchronously
    - All four alerts complete independently
    - Each result contains the triage output
    - /health shows the correct total count
    """
    triage_answer = (
        "Severity: P2\n"
        "Root cause: Recent deployment or upstream dependency issue.\n"
        "Action: Check logs, assess rollback.\n"
        "Escalate: Yes if not resolved in 10 minutes."
    )
    configure(answer=triage_answer)

    task_ids = []
    async with httpx.AsyncClient() as client:
        # Fire all 4 alerts without waiting — that's the point
        for alert in ALL_ALERTS:
            resp = await client.post(f"{runtime_url_for_alerts}/events", json={
                "query": OPS_TRIAGE_PROMPT,
                "persona": "ops_agent",
                "trigger": alert["trigger"],
                "context": alert["context"],
            })
            assert resp.status_code == 202, f"Expected 202, got {resp.status_code}"
            task_ids.append(resp.json()["task_id"])

    # Wait for all four to complete
    results = await asyncio.gather(*[
        poll_until_done(runtime_url_for_alerts, tid, timeout=15.0)
        for tid in task_ids
    ])

    # All completed
    for i, (alert, result) in enumerate(zip(ALL_ALERTS, results)):
        assert result["status"] == "completed", (
            f"Alert {alert['trigger']} did not complete: {result}"
        )
        assert result["output"] == triage_answer

    # Fake CUGA received all 4 queries with the right context
    received = get_received_queries()
    assert len(received) == 4

    received_queries_text = " ".join(q["query"] for q in received)
    # Each service's name should appear in the queries sent
    assert "auth-api" in received_queries_text
    assert "payment-service" in received_queries_text
    assert "search-api" in received_queries_text
    assert "recommendation-engine" in received_queries_text

    # Health check shows all 4 tasks were seen
    async with httpx.AsyncClient() as client:
        health = (await client.get(f"{runtime_url_for_alerts}/health")).json()
    assert health["total_tasks_seen"] == 4
    assert health["queue_depth"] == 0  # all processed
