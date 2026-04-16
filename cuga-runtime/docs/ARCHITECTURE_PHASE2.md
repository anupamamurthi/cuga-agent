# cuga-runtime Architecture — Phase 2

> **What changes:** The asyncio.Queue is replaced by a Postgres-backed persistent queue. Everything else — `worker.py`, `client.py`, `app.py`, `schemas.py` — is unchanged. CUGA core does not change.

Phase 1 is a single process with an in-memory queue. Tasks are lost if the process restarts. One worker handles everything sequentially. Phase 2 fixes all three of those constraints in a single file swap.

---

## What changes, and what doesn't

```
                Phase 1                          Phase 2
                ────────────────────             ────────────────────────────────

queue.py        asyncio.Queue                    Postgres table: cuga_tasks
                + in-memory dict                 SELECT FOR UPDATE SKIP LOCKED

worker.py       unchanged ──────────────────────────────────────────────────────►
client.py       unchanged ──────────────────────────────────────────────────────►
app.py          unchanged ──────────────────────────────────────────────────────►
schemas.py      unchanged ──────────────────────────────────────────────────────►

Infrastructure  none                             Postgres (one table, one index)
Workers         1 (can't scale out)              N (run as many as you want)
Durability      lost on restart                  survives restart, crash, deploy
Retry           no                               yes — exponential backoff
Auth            no                               API key header (FastAPI dep)
```

---

## The big picture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  External data sources                                                       ║
║  RSS feeds  ·  IT alerts  ·  CRM webhooks  ·  Scheduled jobs  ·  …         ║
╚════════════════════════════╤═════════════════════════════════════════════════╝
                             │
                             ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  Adapter services  (one per use case)                                        ║
║  POST /events  { query, trigger, context }  →  202 + task_id                ║
╚════════════════════════════╤═════════════════════════════════════════════════╝
                             │
                             ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  cuga-runtime  (Phase 2)                                                     ║
║                                                                              ║
║   ┌─────────────┐   INSERT row    ┌──────────────────────────────────────┐  ║
║   │  FastAPI    │ ──────────────► │  Postgres  cuga_tasks table          │  ║
║   │             │                 │                                      │  ║
║   │  POST /events│  SELECT result  │  id · status · query · context      │  ║
║   │  GET  /events│ ◄────────────── │  output · error · retry_count       │  ║
║   │  GET  /health│                 │  created_at · completed_at          │  ║
║   └─────────────┘                 └────────────────┬─────────────────────┘  ║
║                                                    │                         ║
║                        ┌───────────────────────────┤                         ║
║                        │ SELECT FOR UPDATE          │                         ║
║                        │ SKIP LOCKED                │                         ║
║             ┌──────────▼──────┐        ┌───────────▼──────┐                ║
║             │  TaskWorker #1  │        │  TaskWorker #2   │   … N workers  ║
║             │                 │        │                  │                  ║
║             │  pull → RUNNING │        │  pull → RUNNING  │                  ║
║             │  call CUGA      │        │  call CUGA       │                  ║
║             │  write result   │        │  write result    │                  ║
║             └────────┬────────┘        └────────┬─────────┘                ║
╚══════════════════════╪══════════════════════════╪═══════════════════════════╝
                       │  HTTP POST /stream        │
                       └──────────┬────────────────┘
                                  ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)                                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Data flow — step by step

```
 ADAPTER SERVICE        FASTAPI (/events)     POSTGRES (cuga_tasks)    WORKER #N          CUGA
       │                      │                       │                    │                 │
       │── POST /events ──────►│                       │                    │                 │
       │   { query, context }  │                       │                    │                 │
       │                      │── INSERT row ─────────►│                    │                 │
       │                      │   status=QUEUED        │                    │                 │
       │◄── 202 { task_id } ──│                       │                    │                 │
       │                      │                       │                    │                 │
       │                      │                       │◄─ SELECT FOR ──────│  (worker polls  │
       │                      │                       │   UPDATE           │   every 500ms)  │
       │                      │                       │   SKIP LOCKED ─────►│                 │
       │                      │                       │── row locked ──────►│                 │
       │                      │                       │◄─ UPDATE status ───│                 │
       │                      │                       │   =RUNNING         │                 │
       │                      │                       │                    │── POST /stream ─►│
       │                      │                       │                    │                 │
       │── GET /events/{id} ──►│                       │                    │◄── SSE Answer ──│
       │◄── { running } ──────│◄── SELECT ────────────│                    │                 │
       │                      │                       │◄─ UPDATE status ───│                 │
       │                      │                       │   =COMPLETED       │                 │
       │                      │                       │   output=<text>    │  (or retry if   │
       │── GET /events/{id} ──►│                       │                    │   CUGA failed)  │
       │◄── { completed,      │◄── SELECT ────────────│                    │                 │
       │     output } ────────│                       │                    │                 │
```

---

## Postgres schema

```sql
CREATE TABLE cuga_tasks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','completed','failed','hitl_required')),

    -- What was submitted
    query         TEXT NOT NULL,
    persona       TEXT NOT NULL DEFAULT 'default',
    trigger       TEXT NOT NULL DEFAULT 'manual',
    context       JSONB NOT NULL DEFAULT '{}',
    callback_url  TEXT,
    thread_id     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- What came back
    output        TEXT,
    error         TEXT,
    completed_at  TIMESTAMPTZ,

    -- Retry state
    retry_count   INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ
);

-- Workers compete for rows using this index
CREATE INDEX idx_cuga_tasks_queued
    ON cuga_tasks (created_at)
    WHERE status = 'queued';
```

---

## Worker selection — how N workers share the queue without stepping on each other

Each worker runs this loop:

```sql
-- Pick exactly one unclaimed task
BEGIN;

UPDATE cuga_tasks
SET    status = 'running'
WHERE  id = (
    SELECT id FROM cuga_tasks
    WHERE  status = 'queued'
      AND  (next_retry_at IS NULL OR next_retry_at <= NOW())
    ORDER  BY created_at
    LIMIT  1
    FOR UPDATE SKIP LOCKED   -- other workers skip rows already locked
)
RETURNING *;

COMMIT;
```

`SKIP LOCKED` means two workers never process the same row. No application-level locking. No leader election. Just Postgres.

---

## Retry strategy

```
FAILED attempt
      │
      │  retry_count < MAX_RETRIES?
      ├── yes ──► set status=queued, retry_count++,
      │           next_retry_at = NOW() + 2^retry_count seconds
      │           (1s, 2s, 4s, 8s, 16s…)
      │
      └── no  ──► set status=failed permanently
                  fire callback_url with error (if set)
```

| Attempt | Delay before retry |
|---------|-------------------|
| 1st failure | 1 second |
| 2nd failure | 2 seconds |
| 3rd failure | 4 seconds |
| 4th failure | 8 seconds |
| 5th failure | permanent FAILED |

---

## API key authentication

Phase 2 adds a simple `X-API-Key` header check as a FastAPI dependency. All endpoints that write (`POST /events`) require it. Read endpoints (`GET /events`, `GET /health`) remain open for monitoring tools.

```
POST /events
  Headers:  X-API-Key: <secret>
  Body:     { query, persona, trigger, context }
```

Configure via environment variable:
```bash
CUGA_API_KEY=your-secret-key uvicorn cuga_runtime.app:app --port 8001
```

---

## Running multiple workers

Workers are stateless — they only need a Postgres connection string. Run as many as needed:

```bash
# Worker 1
CUGA_DB_URL=postgresql://... CUGA_API_URL=http://localhost:7860 \
  python -m cuga_runtime.worker

# Worker 2 (separate process or container)
CUGA_DB_URL=postgresql://... CUGA_API_URL=http://localhost:7860 \
  python -m cuga_runtime.worker

# Or with Docker Compose / Kubernetes — scale the worker service horizontally
```

Throughput scales linearly with CUGA's own concurrency limit.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CUGA_DB_URL` | — | Postgres connection string (required) |
| `CUGA_API_URL` | `http://localhost:7860` | CUGA server URL |
| `CUGA_TIMEOUT` | `300` | Seconds to wait for CUGA |
| `CUGA_API_KEY` | — | Required header for POST /events |
| `CUGA_MAX_RETRIES` | `4` | Max attempts before permanent failure |
| `CUGA_WORKER_POLL_MS` | `500` | How often workers check for new tasks |

---

## Phase 1 → Phase 2 migration

```
1. Add Postgres (docker run -e POSTGRES_DB=cuga postgres:16, or managed)
2. Run the schema migration (one SQL file, no data migration needed)
3. Set CUGA_DB_URL
4. Set CUGA_API_KEY
5. Deploy — existing callers need only add the X-API-Key header
   Everything else (event shape, task_ids, polling pattern) is identical
```

No changes to adapter services, no changes to CUGA.

---

## What Phase 3 adds

Phase 2 gives you durability and horizontal scale. Phase 3 adds real-time streaming to the UI (no polling), HITL (human-in-the-loop pausing), priority routing, and push delivery via a proper message bus. See `ARCHITECTURE_PHASE3.md`.
