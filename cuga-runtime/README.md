# cuga-runtime

Event-driven async layer for CUGA — Phase 1.

External systems post events and get an immediate `task_id` back. A background worker picks each event up, calls CUGA, and stores the result. Callers poll for it or supply a callback URL to receive it automatically.

No new infrastructure. No Kafka. No Redis. Just Python + a running CUGA server.

---

## What it does

```
External System (CRM, scheduler, webhook...)
        │
        │  POST /events  {"query": "...", "persona": "...", "context": {...}}
        ↓
  cuga-runtime  (this package)
        │  returns 202 + task_id immediately
        │
        │  background worker picks up the task
        ↓
  CUGA /stream  (your running CUGA server)
        │
        │  answer
        ↓
  GET /status/{task_id}  →  { "status": "completed", "output": "..." }
       OR
  POST callback_url  (if you supplied one)
```

---

## Installation

```bash
cd cuga-runtime
pip install -e ".[dev]"
# or with uv:
uv pip install -e ".[dev]"
```

---

## Configuration

| Variable        | Default                    | Description                        |
|-----------------|----------------------------|------------------------------------|
| `CUGA_API_URL`  | `http://localhost:8000`    | URL of the running CUGA server     |
| `CUGA_TIMEOUT`  | `300`                      | Seconds to wait for CUGA response  |

---

## Running the server

First, make sure CUGA is running:

```bash
# In the cuga-agent-apr10 directory
cuga start   # or however you normally start CUGA
```

Then start cuga-runtime:

```bash
cd cuga-runtime
CUGA_API_URL=http://localhost:8000 uvicorn cuga_runtime.app:app --port 8001 --reload
```

---

## Trying it out

### 1. Submit an event

```bash
curl -s -X POST http://localhost:8001/events \
  -H "Content-Type: application/json" \
  -d '{
    "query": "A new sales lead has arrived. Score it 1-10 and suggest a next action.",
    "persona": "sales_assistant",
    "trigger": "crm.lead_created",
    "context": {
      "company": "Acme Corp",
      "contact": "Jane Smith, VP Engineering",
      "deal_size": "$80k ARR",
      "source": "Inbound demo request"
    }
  }'
```

Response (immediate):
```json
{ "task_id": "3f2e1d0c-...", "status": "queued" }
```

### 2. Poll for the result

```bash
curl -s http://localhost:8001/status/3f2e1d0c-...
```

While running:
```json
{ "task_id": "...", "status": "running" }
```

When done:
```json
{
  "task_id": "...",
  "status": "completed",
  "output": "Lead Score: 8/10\n\nKey Reasons: ...\n\nNext Action: ...",
  "thread_id": "3f2e1d0c-...",
  "completed_at": "2026-04-14T10:23:45"
}
```

### 3. Check the queue

```bash
curl -s http://localhost:8001/health
```

```json
{
  "status": "ok",
  "queue_depth": 0,
  "total_tasks_seen": 3,
  "cuga_url": "http://localhost:8000"
}
```

### 4. Use a callback instead of polling

Include `"callback_url"` in your event and the runtime will POST the result to you when CUGA finishes:

```bash
curl -X POST http://localhost:8001/events \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Summarise the open tickets for account ACME-001",
    "persona": "support_agent",
    "trigger": "ticket.escalated",
    "callback_url": "https://your-app.example.com/webhooks/cuga"
  }'
```

---

## Running the tests

### Unit tests (no CUGA server needed)

```bash
cd cuga-runtime
pytest tests/ -v
```

Expected output:

```
tests/test_schemas.py::TestCugaTaskEvent::test_minimal_event_has_defaults PASSED
tests/test_schemas.py::TestCugaTaskEvent::test_task_ids_are_unique PASSED
tests/test_schemas.py::TestCugaTaskEvent::test_caller_supplied_task_id_is_preserved PASSED
tests/test_schemas.py::TestCugaTaskEvent::test_context_is_stored PASSED
tests/test_schemas.py::TestCugaTaskEvent::test_round_trip_json PASSED
tests/test_schemas.py::TestCugaResultEvent::test_queued_result PASSED
tests/test_schemas.py::TestCugaResultEvent::test_completed_result PASSED
tests/test_schemas.py::TestCugaResultEvent::test_failed_result PASSED
tests/test_queue.py::test_put_sets_queued_status PASSED
...
tests/integration/test_crm_lead_flow.py::test_crm_lead_flow_mock PASSED
tests/integration/test_crm_lead_flow.py::test_crm_lead_flow_callback PASSED
tests/integration/test_crm_lead_flow.py::test_multiple_leads_processed_in_order PASSED
```

### Integration test against a live CUGA server

```bash
CUGA_LIVE=1 CUGA_API_URL=http://localhost:8000 pytest tests/integration/ -s -k live -v
```

This sends a real CRM lead event to CUGA and prints the qualification response.

---

## What each file does

```
cuga_runtime/
  schemas.py   Data models — CugaTaskEvent, CugaResultEvent, TaskStatus
  queue.py     In-memory task queue + result store
  client.py    Thin async HTTP client for CUGA's /stream SSE endpoint
  worker.py    Background loop: queue → CUGA → result
  app.py       FastAPI app — /events, /status/{id}, /health

tests/
  test_schemas.py      Schema validation and serialisation
  test_queue.py        Queue put/get/result lifecycle
  test_worker.py       Worker success, failure, callback, context folding
  test_app.py          HTTP endpoint behaviour (no server needed)
  integration/
    test_crm_lead_flow.py   End-to-end CRM lead qualification flow
```

---

## Upgrading to Phase 2 (DB-backed queue)

When you need durability across restarts:

1. Replace `queue.py` with a Postgres-backed implementation (see `cuga_event_driven_progression.md` Stage 2)
2. `worker.py`, `app.py`, and all tests remain unchanged
3. The `CugaTaskEvent` schema is the same

The migration is a one-file swap. CUGA core does not change.
