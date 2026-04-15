# CUGA Event-Driven Progression
## From Webhooks to Kafka — A Crawl-Walk-Run-Fly Architecture

---

## The Core Principle

CUGA core never changes. At every stage, the progression is entirely in the **runtime layer** — a thin adapter that sits between the outside world and the CUGA API. The event schema (`CugaTaskEvent`) is defined once and survives the full journey.

```
External World → [Runtime Adapter] → CUGA REST/SDK → Response → [Runtime Adapter] → External World
                        ↑
              This is the only thing that changes
              across all four stages
```

---

## Stage Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   STAGE 0          STAGE 1          STAGE 2          STAGE 3               │
│   Today            Webhooks         DB Queue          Redis Streams         │
│   Synchronous      Fire & Forget    Durable Tasks     Multi-Worker         │
│                                                                             │
│   Direct API   →   HTTP Events  →   Postgres/SQLite →  Redis Consumer     │
│   call             endpoint         task table          Groups              │
│                                                                             │
│   No new infra     No new infra     No new infra      Redis                │
│                    (~1 day)         (~1 sprint)        (likely in stack)    │
│                                                                             │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ↓  When scale/replay/ecosystem demands it
                         ┌──────────────────────┐
                         │       STAGE 4        │
                         │   Kafka / Confluent  │
                         │   Full Streaming     │
                         └──────────────────────┘
```

---

## Shared Foundation: The Event Schema

Defined once. Survives every stage unchanged.

```python
# runtime/schemas.py — defined at Stage 1, carried through to Stage 4
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uuid, datetime

class CugaTaskEvent(BaseModel):
    task_id: str = str(uuid.uuid4())
    persona: str                          # which CUGA persona to invoke
    trigger: str                          # e.g. "inventory.low", "ticket.created"
    payload: Dict[str, Any]               # event-specific data
    priority: int = 5                     # 1 (highest) to 10 (lowest)
    idempotency_key: Optional[str] = None # caller-supplied dedup key
    created_at: str = datetime.datetime.utcnow().isoformat()

class CugaResultEvent(BaseModel):
    task_id: str
    persona: str
    status: str                           # "completed" | "failed" | "hitl_required"
    output: Optional[str] = None
    error: Optional[str] = None
    hitl_thread_id: Optional[str] = None  # for human-in-the-loop handoff
    completed_at: str = datetime.datetime.utcnow().isoformat()
```

---

## Stage 0: Today — Synchronous API

**What it is:** Caller invokes CUGA directly, waits for response.

**Architecture:**
```
CRM / App / Script
      │
      │  POST /invoke  (waits...)
      ↓
   CUGA API
      │
      │  (agent reasoning, tool calls...)
      ↓
   Response JSON  ──────────────────────→  Caller
```

**Characteristics:**
- Simple, works today
- Caller blocks until agent finishes (can be 10–120 seconds)
- Caller must handle retries, timeouts
- No parallelism across tasks
- No audit trail beyond application logs

**When this breaks down:** When you have more than a handful of concurrent callers, or when callers can't afford to block (mobile apps, webhooks from external systems, batch pipelines).

---

## Stage 1: Webhook Events (Fire and Forget)

**What changes:** CUGA runtime exposes an `/events` HTTP endpoint. Callers POST an event and get an immediate `202 Accepted`. A background thread processes the task asynchronously and pushes the result to a callback URL.

**New infrastructure needed:** None. This is pure Python.

**Architecture:**
```
CRM / App / Script
      │
      │  POST /events  (returns 202 immediately)
      ↓
 ┌────────────────────────────────────────┐
 │         CUGA Streaming Runtime         │
 │                                        │
 │   /events endpoint                     │
 │       │ enqueues task                  │
 │       ↓                                │
 │   asyncio.Queue (in-memory)            │
 │       │ worker pulls                   │
 │       ↓                                │
 │   Worker → CUGA REST API              │
 │               │ result                 │
 │               ↓                        │
 │   POST callback_url (result)           │
 └────────────────────────────────────────┘
```

**Implementation:**

```python
# runtime/stage1_webhook.py
from fastapi import FastAPI, BackgroundTasks
from runtime.schemas import CugaTaskEvent, CugaResultEvent
import asyncio, httpx

app = FastAPI()
_queue: asyncio.Queue = asyncio.Queue()

@app.post("/events", status_code=202)
async def submit_event(event: CugaTaskEvent, background_tasks: BackgroundTasks):
    """Caller fires and forgets. Returns task_id immediately."""
    await _queue.put(event)
    return {"task_id": event.task_id, "status": "queued"}

@app.on_event("startup")
async def start_worker():
    asyncio.create_task(_worker())

async def _worker():
    while True:
        event = await _queue.get()
        result = await _invoke_cuga(event)
        if event.payload.get("callback_url"):
            await _send_callback(event.payload["callback_url"], result)

async def _invoke_cuga(event: CugaTaskEvent) -> CugaResultEvent:
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            "http://cuga-api/invoke",
            json={"persona": event.persona, "input": event.payload, "thread_id": event.task_id}
        )
        data = resp.json()
        return CugaResultEvent(
            task_id=event.task_id,
            persona=event.persona,
            status="completed",
            output=data.get("output")
        )

async def _send_callback(url: str, result: CugaResultEvent):
    async with httpx.AsyncClient() as client:
        await client.post(url, json=result.dict())
```

**What you gain:**
- Callers no longer block
- Tasks survive caller disconnects
- Simple horizontal scaling (add more CUGA instances, not more workers)
- Foundation for all future stages

**Limitation:** If the runtime process restarts, the in-memory queue is lost. Tasks in flight are dropped.

**Milestone trigger:** You've shipped event-driven CUGA. One afternoon of work.

---

## Stage 2: Database-Backed Queue (Durable Tasks)

**What changes:** Replace `asyncio.Queue` with a `cuga_tasks` table. Tasks survive process restarts. You get retries, dead-letter, visibility into queue depth, and per-task status.

**New infrastructure needed:** None if Postgres or SQLite is already in the stack (it almost always is).

**Architecture:**
```
External System
      │
      │  POST /events
      ↓
 ┌────────────────────────────────────────────────────────┐
 │                 CUGA Streaming Runtime                  │
 │                                                        │
 │   /events endpoint                                     │
 │       │ INSERT INTO cuga_tasks                         │
 │       ↓                                                │
 │   ┌──────────────────────────────────────────────┐    │
 │   │              cuga_tasks table                │    │
 │   │  task_id | status  | payload | attempts | …  │    │
 │   │  abc123  | pending | {...}   | 0         |   │    │
 │   │  def456  | running | {...}   | 1         |   │    │
 │   │  ghi789  | failed  | {...}   | 3         |   │    │
 │   └──────────────────────────────────────────────┘    │
 │       │ polling worker (SELECT FOR UPDATE SKIP LOCKED)│
 │       ↓                                                │
 │   Worker → CUGA REST API → UPDATE status              │
 └────────────────────────────────────────────────────────┘
```

**Implementation:**

```python
# runtime/stage2_db_queue.py
import asyncio, datetime
from sqlalchemy import text
from runtime.schemas import CugaTaskEvent, CugaResultEvent

# --- Schema (run once at startup) ---
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cuga_tasks (
    task_id        TEXT PRIMARY KEY,
    persona        TEXT NOT NULL,
    trigger        TEXT NOT NULL,
    payload        JSONB NOT NULL,
    priority       INT DEFAULT 5,
    status         TEXT DEFAULT 'pending',   -- pending | running | completed | failed | dead
    attempts       INT DEFAULT 0,
    max_attempts   INT DEFAULT 3,
    result         JSONB,
    error          TEXT,
    created_at     TIMESTAMP DEFAULT NOW(),
    started_at     TIMESTAMP,
    completed_at   TIMESTAMP,
    visible_after  TIMESTAMP DEFAULT NOW()   -- backoff / delayed retry
);
CREATE INDEX IF NOT EXISTS idx_cuga_tasks_pending 
    ON cuga_tasks (priority, created_at) 
    WHERE status = 'pending' AND visible_after <= NOW();
"""

# --- Enqueue ---
async def enqueue(db, event: CugaTaskEvent):
    await db.execute(text("""
        INSERT INTO cuga_tasks (task_id, persona, trigger, payload, priority)
        VALUES (:task_id, :persona, :trigger, :payload::jsonb, :priority)
        ON CONFLICT (task_id) DO NOTHING
    """), event.dict())

# --- Worker polling loop ---
async def poll_worker(db, cuga_client):
    while True:
        async with db.begin():
            # Claim one task atomically — SKIP LOCKED prevents double-processing
            row = await db.execute(text("""
                SELECT task_id, persona, payload FROM cuga_tasks
                WHERE status = 'pending' AND visible_after <= NOW()
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """))
            task = row.fetchone()

            if not task:
                await asyncio.sleep(2)  # nothing to do, wait
                continue

            # Mark running
            await db.execute(text("""
                UPDATE cuga_tasks 
                SET status='running', started_at=NOW(), attempts=attempts+1
                WHERE task_id=:task_id
            """), {"task_id": task.task_id})

        # Invoke CUGA (outside transaction)
        try:
            result = await cuga_client.invoke(task.persona, task.payload)
            await _mark_completed(db, task.task_id, result)
        except Exception as e:
            await _mark_failed_or_retry(db, task.task_id, str(e))

async def _mark_failed_or_retry(db, task_id, error):
    async with db.begin():
        row = await db.execute(text(
            "SELECT attempts, max_attempts FROM cuga_tasks WHERE task_id=:id"
        ), {"id": task_id})
        t = row.fetchone()
        if t.attempts >= t.max_attempts:
            # Dead letter
            await db.execute(text(
                "UPDATE cuga_tasks SET status='dead', error=:error WHERE task_id=:id"
            ), {"error": error, "id": task_id})
        else:
            # Exponential backoff retry
            backoff = datetime.timedelta(seconds=2 ** t.attempts)
            await db.execute(text("""
                UPDATE cuga_tasks 
                SET status='pending', error=:error, 
                    visible_after=NOW() + :backoff
                WHERE task_id=:id
            """), {"error": error, "backoff": backoff, "id": task_id})
```

**What you gain:**
- **Durability:** Process restart drops nothing
- **Retries with backoff:** Built in, configurable per-task
- **Dead-letter queue:** Failed tasks are visible, inspectable, replayable
- **Observability:** `SELECT COUNT(*), status FROM cuga_tasks GROUP BY status` is your queue dashboard
- **Idempotency:** `ON CONFLICT DO NOTHING` on `task_id`
- **Priority queue:** Higher-priority tasks processed first

**Operational benefit:** Any engineer can inspect and operate the queue with plain SQL. No new tooling to learn.

**Limitation:** The polling loop adds 0–2 seconds latency. Multiple workers need careful coordination (handled by `SELECT FOR UPDATE SKIP LOCKED`). Throughput tops out at a few hundred tasks/minute before Postgres becomes the bottleneck.

**Milestone trigger:** You have durable, production-grade task processing. Multiple CUGA workers can run safely. This is sufficient for most enterprise workloads.

---

## Stage 3: Redis Streams (Multi-Worker, Low Latency)

**What changes:** Replace the Postgres poll loop with Redis Streams. Redis Streams give you Kafka-like consumer groups and acknowledgment semantics with much lower operational overhead.

**New infrastructure needed:** Redis (version 5+). Most teams already have it for caching or session storage.

**Architecture:**
```
External System
      │  XADD cuga:tasks
      ↓
 ┌─────────────────────────────────────────────────────────────────┐
 │                      CUGA Streaming Runtime                      │
 │                                                                  │
 │   ┌─────────────────────────────────────────────────────────┐   │
 │   │                  Redis Stream: cuga:tasks               │   │
 │   │   1234-0: {task_id, persona, trigger, payload}          │   │
 │   │   1235-0: {task_id, persona, trigger, payload}          │   │
 │   │   1236-0: {task_id, persona, trigger, payload}          │   │
 │   └──────────────────────┬──────────────────────────────────┘   │
 │                          │ Consumer Group: cuga-workers          │
 │              ┌───────────┼───────────┐                          │
 │              ↓           ↓           ↓                          │
 │           Worker-1    Worker-2    Worker-3                       │
 │              │           │           │                          │
 │              └─────────CUGA API──────┘                          │
 │                          │                                       │
 │                    XADD cuga:results                             │
 └─────────────────────────────────────────────────────────────────┘
```

**Implementation:**

```python
# runtime/stage3_redis_streams.py
import asyncio, json
import aioredis
from runtime.schemas import CugaTaskEvent, CugaResultEvent

TASK_STREAM   = "cuga:tasks"
RESULT_STREAM = "cuga:results"
DLQ_STREAM    = "cuga:dlq"
CONSUMER_GROUP = "cuga-workers"

async def setup_streams(redis):
    """Create consumer group once (idempotent)."""
    for stream in [TASK_STREAM, RESULT_STREAM]:
        try:
            await redis.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
        except aioredis.ResponseError:
            pass  # group already exists

# --- Enqueue ---
async def enqueue(redis, event: CugaTaskEvent):
    await redis.xadd(TASK_STREAM, {
        "data": event.json(),
        "priority": event.priority,
    })

# --- Worker ---
async def worker(worker_id: str, redis, cuga_client):
    while True:
        # XREADGROUP blocks until a message is available (no polling delay)
        messages = await redis.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=worker_id,
            streams={TASK_STREAM: ">"},   # ">" means only new messages
            count=1,
            block=5000                    # block up to 5s, then loop
        )
        if not messages:
            await _reclaim_stale(worker_id, redis, cuga_client)
            continue

        stream_name, entries = messages[0]
        for entry_id, fields in entries:
            event = CugaTaskEvent.parse_raw(fields["data"])
            try:
                result = await cuga_client.invoke(event.persona, event.payload)
                await redis.xadd(RESULT_STREAM, {"data": result.json()})
                await redis.xack(TASK_STREAM, CONSUMER_GROUP, entry_id)  # mark done
            except Exception as e:
                await _handle_failure(redis, entry_id, event, str(e))

async def _reclaim_stale(worker_id, redis, cuga_client):
    """Re-process messages that have been in-flight too long (dead worker scenario)."""
    pending = await redis.xautoclaim(
        TASK_STREAM, CONSUMER_GROUP, worker_id,
        min_idle_time=60_000,  # reclaim messages idle > 60s
        start_id="0-0", count=5
    )
    # ... process reclaimed messages same as above

async def _handle_failure(redis, entry_id, event, error):
    # Move to DLQ after max retries
    attempt_key = f"cuga:attempts:{event.task_id}"
    attempts = await redis.incr(attempt_key)
    await redis.expire(attempt_key, 3600)
    if attempts >= 3:
        await redis.xadd(DLQ_STREAM, {"data": event.json(), "error": error})
        await redis.xack(TASK_STREAM, CONSUMER_GROUP, entry_id)
    # else: leave in pending list, another worker will reclaim it
```

**What you gain over Stage 2:**
- **Sub-millisecond event delivery** (no polling delay)
- **True consumer groups:** Workers share the stream — each message is delivered to exactly one worker
- **Automatic rebalancing:** Add workers without coordination; Redis distributes automatically
- **Built-in DLQ semantics:** Pending Entries List (PEL) tracks unacknowledged messages
- **Dead worker recovery:** `XAUTOCLAIM` reclaims messages from crashed workers
- **Throughput:** Easily handles tens of thousands of tasks/minute

**Operational note:** Redis Streams are visible in Redis Insight. `XLEN cuga:tasks` is queue depth. `XPENDING` shows stuck messages.

**Limitation:** Redis is in-memory (though persistence is configurable). For strict durability guarantees or multi-datacenter replication, Kafka starts to make sense.

---

## Stage 4: Kafka (Full Event Streaming)

**When you actually need this:**
- You need **event replay** (re-run all events from Day 1 through a new agent persona)
- You have **multiple independent consumers** per event (e.g., audit pipeline + AI agent + analytics all consuming the same inventory event)
- You need **stream processing** (Flink/ksqlDB) to enrich events before agent invocation
- You're operating at **Confluent Platform scale** — schema registry, governance, multi-datacenter
- Your organization has a **central event mesh** (data platform team owns Kafka; you're plugging CUGA in)

**Architecture:**
```
Enterprise Event Mesh (Confluent Platform)
      │
      │  inventory.transactions, ticket.created, order.updated, …
      ↓
 ┌──────────────────────────────────────────────────────────────────┐
 │                      CUGA Streaming Runtime                       │
 │                                                                   │
 │   Kafka Consumer                  ksqlDB                         │
 │   Group: cuga-workers    ←──────  Materializes live state tables │
 │       │                           (query via MCP tools)           │
 │       │                                                           │
 │   Event Translator                Streaming Knowledge             │
 │   (Jinja2 persona templates)      (Flink embedding pipeline)     │
 │       │                               │                           │
 │       ↓                               ↓                           │
 └───────────────────────────────────────────────────────────────────┘
                  │ REST/SDK (unchanged)
                  ↓
      ┌──────────────────────────────┐
      │     CUGA Core (unchanged)    │
      │  LangGraph · Policy · HITL   │
      └──────────────────────────────┘
```

See [cuga_event_driven_roadmap.md](cuga_event_driven_roadmap.md) and [cuga_event_driven_reference_architecture.md](cuga_event_driven_reference_architecture.md) for the full Kafka implementation roadmap and Confluent Platform architecture.

---

## Progression Summary

| | Stage 0 | Stage 1 | Stage 2 | Stage 3 | Stage 4 |
|---|---|---|---|---|---|
| **Name** | Synchronous | Webhooks | DB Queue | Redis Streams | Kafka |
| **New infra** | None | None | None (Postgres) | Redis | Kafka cluster |
| **Delivery** | Blocking | Async HTTP | Poll (0–2s lag) | Push (<1ms lag) | Push (<1ms lag) |
| **Durability** | Caller's problem | Lost on restart | ✓ Durable | Configurable | ✓ Durable + replay |
| **Retries** | Caller's problem | Manual | ✓ Built-in | ✓ Built-in | ✓ Built-in |
| **Dead letter** | None | None | ✓ `dead` rows | ✓ DLQ stream | ✓ DLQ topic |
| **Multi-worker** | N/A | Yes (stateless) | ✓ SKIP LOCKED | ✓ Consumer groups | ✓ Consumer groups |
| **Throughput** | Low | Medium | ~100s/min | ~10Ks/min | Millions/min |
| **Event replay** | No | No | Manual (re-enqueue) | No | ✓ Native |
| **Stream processing** | No | No | No | No | ✓ Flink/ksqlDB |
| **Operational cost** | Zero | Zero | Low | Low–Medium | High |
| **CUGA core changes** | None | None | None | None | None (+ optional ActivityEmitter) |

---

## Decision Guide: When to Move to the Next Stage

```
Are callers blocking on CUGA responses?
  YES → Move to Stage 1 (Webhooks)

Are tasks being lost on process restarts?
  YES → Move to Stage 2 (DB Queue)

Is queue depth > 500 concurrent tasks, OR need sub-second delivery?
  YES → Move to Stage 3 (Redis Streams)

Do any of these apply?
  - Central enterprise Kafka mesh exists and IT wants CUGA plugged in
  - Need to replay historical events through CUGA
  - Multiple independent consumers per event (agent + audit + analytics)
  - Need ksqlDB stream materialization for live state queries
  - Need Flink enrichment pipeline upstream of agent
  YES → Move to Stage 4 (Kafka)
```

---

## Key Observation

The same `CugaTaskEvent` schema, the same worker logic, and the same CUGA REST API invocation survive all four stages. **The only thing that changes is what delivers the event to the worker.** This means:

1. You can migrate between stages without touching CUGA core
2. You can run multiple stages simultaneously (some integrations on DB queue, some on Redis, some on Kafka)
3. You can build and validate the agent behavior at Stage 1, then swap infrastructure underneath without regression

The event-driven CUGA story is not "integrate Kafka." It is "adopt an event-shaped interface today, and let the infrastructure underneath grow with you."
