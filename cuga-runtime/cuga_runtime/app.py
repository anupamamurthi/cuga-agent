"""
CUGA Runtime — FastAPI application.

Endpoints
---------
POST /events              Submit a task. Returns task_id immediately (202 Accepted).
GET  /events              List events with optional filters.
GET  /events/{task_id}    Get a single event by ID.
GET  /health              Liveness check + queue depth.

Configuration (environment variables)
--------------------------------------
CUGA_API_URL    URL of the running CUGA server. Default: http://localhost:8000
CUGA_TIMEOUT    Seconds to wait for CUGA to respond. Default: 300
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cuga_runtime.client import CugaClient
from cuga_runtime.queue import TaskQueue
from cuga_runtime.schemas import CugaResultEvent, CugaTaskEvent, TaskStatus
from cuga_runtime.worker import TaskWorker


# ── Shared state ──────────────────────────────────────────────────────────────

task_queue: TaskQueue = TaskQueue()
cuga_client: CugaClient = CugaClient(
    base_url=os.getenv("CUGA_API_URL", "http://localhost:8000"),
    timeout=float(os.getenv("CUGA_TIMEOUT", "300")),
)
worker: TaskWorker = TaskWorker(queue=task_queue, cuga_client=cuga_client)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(worker.run())
    yield
    worker.stop()
    await asyncio.wait_for(worker_task, timeout=5.0)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CUGA Runtime",
    description="Event-driven async layer for CUGA — Phase 1",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/events", status_code=202)
async def submit_event(event: CugaTaskEvent) -> dict:
    """
    Submit a task for async processing.

    Returns immediately with task_id and status=queued.
    Use GET /events/{task_id} to poll, or supply callback_url to receive the
    result pushed when done.

    Example:
        {
          "query": "Triage this alert.",
          "persona": "ops_agent",
          "trigger": "alert.cpu_high",
          "context": { "service": "auth-api", "cpu": "94%" },
          "callback_url": "https://my-app.com/webhook"
        }
    """
    await task_queue.put(event)
    return {"task_id": event.task_id, "status": TaskStatus.QUEUED}


@app.get("/events")
async def list_events(
    status: Optional[str] = Query(None, description="Filter by status: queued | running | completed | failed"),
    trigger: Optional[str] = Query(None, description="Filter by trigger name, e.g. alert.cpu_high"),
    limit: int = Query(50, ge=1, le=500, description="Max results to return"),
) -> JSONResponse:
    """
    List all events seen since the process started.

    Returns counts by status plus a filtered list of events, newest first.

    Examples:
        GET /events                          — all events
        GET /events?status=failed            — only failed events
        GET /events?trigger=alert.cpu_high   — events from one trigger
        GET /events?status=completed&limit=10 — last 10 completed
    """
    counts = task_queue.counts_by_status()
    events = []

    for result in task_queue.all_results():
        original = task_queue.get_event(result.task_id)

        if status and result.status.value != status:
            continue
        if trigger and (original is None or original.trigger != trigger):
            continue

        events.append(_event_dict(original, result))

        if len(events) >= limit:
            break

    return JSONResponse({"counts": counts, "events": events})


@app.get("/events/{task_id}")
async def get_event(task_id: str) -> JSONResponse:
    """
    Get the current state of a single event.

    Returns the full event — both what was submitted and the current result.

    Status values:
      queued        — accepted, waiting for the worker
      running       — CUGA is processing it right now
      completed     — done; see output field
      failed        — something went wrong; see error field
      hitl_required — CUGA paused for human input (future phase)
    """
    result = task_queue.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Event '{task_id}' not found")
    original = task_queue.get_event(task_id)
    return JSONResponse(_event_dict(original, result))


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness check. Reports queue depth for backpressure monitoring."""
    return JSONResponse({
        "status": "ok",
        "queue_depth": task_queue.pending_count,
        "total_events_seen": task_queue.total_tasks,
        "cuga_url": cuga_client.base_url,
    })


# ── Backward-compatible alias ─────────────────────────────────────────────────
# Tests and existing integrations that use GET /status/{task_id} keep working.

@app.get("/status/{task_id}", include_in_schema=False)
async def get_status_alias(task_id: str) -> CugaResultEvent:
    result = task_queue.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _event_dict(original: Optional[CugaTaskEvent], result: CugaResultEvent) -> dict:
    """Merge the original submitted event with its current result into one dict."""
    return {
        "task_id": result.task_id,
        "status": result.status.value,
        # What was submitted
        "trigger": original.trigger if original else None,
        "persona": original.persona if original else None,
        "query": original.query if original else None,
        "context": original.context if original else None,
        "created_at": original.created_at if original else None,
        # What came back
        "output": result.output,
        "error": result.error,
        "thread_id": result.thread_id,
        "completed_at": result.completed_at,
    }
