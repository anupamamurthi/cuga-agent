# cuga-runtime Architecture

> **Guiding principle:** CUGA core does not change. The runtime is a thin async adapter between the outside world and CUGA's existing `/stream` endpoint. Everything in this package could be deleted tomorrow and CUGA would keep working.

---

## The big picture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  External data sources                                                       ║
║                                                                              ║
║   RSS / Atom feeds    IT alerts    CRM webhooks    Scheduled jobs    …       ║
╚════════════════════════════╤═════════════════════════════════════════════════╝
                             │  "something happened — please assess this"
                             │
                             ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  Adapter service  (one per use case, you write this)                         ║
║                                                                              ║
║   Knows how to fetch data from the source.                                  ║
║   Translates raw events into a structured CugaTaskEvent.                    ║
║   Example: examples/ai_feed_watcher/service.py  (port 8002)                 ║
║                                                                              ║
║   POST /runtime/events  {                                                    ║
║     query:   "You are monitoring AI blogs. Assess this post…",               ║
║     trigger: "rss.new_post.anthropic",                                       ║
║     context: { title, text, url, feed_name, posted_at }                     ║
║   }                                                                          ║
╚════════════════════════════╤═════════════════════════════════════════════════╝
                             │  HTTP POST  →  202 Accepted + task_id
                             │              (returns immediately, never blocks)
                             ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  cuga-runtime  (port 8001)                                                   ║
║                                                                              ║
║   ┌──────────────┐    put(event)    ┌────────────────┐    get()             ║
║   │  FastAPI     │ ───────────────► │   TaskQueue    │ ──────────────────►  ║
║   │              │                  │                │                      ║
║   │  POST /events│                  │ asyncio.Queue  │   ┌──────────────┐   ║
║   │  GET  /events│ ◄─ get_result ── │ dict[results]  │ ◄─│  TaskWorker  │   ║
║   │  GET  /health│                  └────────────────┘   │              │   ║
║   └──────────────┘                                       │ pull → mark  │   ║
║                                                          │ RUNNING →    │   ║
║                                                          │ call CUGA →  │   ║
║                                                          │ write result │   ║
║                                                          └──────┬───────┘   ║
╚═════════════════════════════════════════════════════════════════╪═══════════╝
                                                                  │ HTTP POST /stream
                                                                  │
                                                                  ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)                                                           ║
║                                                                              ║
║   Receives the full query + folded context as a natural-language string.    ║
║   Reasons, calls tools, and streams back an SSE response:                   ║
║                                                                              ║
║     event: Answer                                                            ║
║     data: { "data": "relevant: yes\nsignal: high\nsummary: …" }             ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Key insight:** cuga-runtime is not an AI feature — it's a queue. It decouples "event arrives" from "CUGA finishes". The adapter service and the UI never wait.

---

## Data flow — RSS feed watcher, step by step

This traces a single Anthropic blog post from RSS fetch to rendered assessment in the UI.

```
 BROWSER UI              FEED WATCHER             CUGA-RUNTIME              CUGA CORE
 (port 8002)             (port 8002)               (port 8001)               (port 7860)
     │                       │                          │                         │
     │── POST /fetch ────────►│                          │                         │
     │   { days: 7,          │                          │                         │
     │     preview_only:true }│                          │                         │
     │                       │── GET rss.xml ──► [Anthropic RSS]                  │
     │                       │◄── XML ──────────────────────────────────────────  │
     │                       │   parse feed, filter by date                        │
     │◄── 200 { posts, ──────│                          │                         │
     │         prompt } ─────│                          │                         │
     │                       │                          │                         │
     │  (user clicks         │                          │                         │
     │   "Analyze" on a card)│                          │                         │
     │── POST /runtime/ ─────►│                          │                         │
     │   events              │── POST /events ──────────►│                         │
     │   { query, trigger,   │   (proxy forward)        │                         │
     │     context }         │                          │── 202 { task_id } ──────►│
     │◄── 202 { task_id } ───│◄── 202 { task_id } ──────│                         │
     │                       │                          │                         │
     │  (poll loop, 4s)      │                          │  worker picks up task   │
     │── GET /runtime/ ──────►│                          │  status → RUNNING       │
     │   events?limit=200    │── GET /events ───────────►│                         │
     │                       │◄── { status:"running" } ─│                         │
     │◄── { running } ───────│                          │                         │
     │   card shows spinner  │                          │                         │
     │                       │                          │── POST /stream ─────────►│
     │                       │                          │   query + context        │
     │                       │                          │◄── SSE: Answer event ────│
     │                       │                          │    { "data":             │
     │                       │                          │      "relevant: yes\n    │
     │                       │                          │       signal: high\n     │
     │                       │                          │       summary: …" }      │
     │                       │                          │  extract "data" field    │
     │                       │                          │  status → COMPLETED      │
     │                       │                          │  output = parsed text    │
     │── GET /runtime/ ──────►│                          │                         │
     │   events?limit=200    │── GET /events ───────────►│                         │
     │◄── { completed, ──────│◄── { completed,          │                         │
     │     output }          │     output } ────────────│                         │
     │  parseAssessment()    │                          │                         │
     │  card → green badge   │                          │                         │
     │  "✓ relevant · high"  │                          │                         │
```

### Status transitions

```
  POST /events accepted
        │
        ▼
    ┌ QUEUED ──────────────────── sitting in asyncio.Queue
        │
        ▼
    ┌ RUNNING ─────────────────── worker has called CUGA /stream, waiting for answer
        │
        ├──► COMPLETED ────────── CUGA returned an Answer event; output field is set
        │
        ├──► FAILED ───────────── CUGA returned Error, HTTP failed, or worker threw
        │
        └──► HITL_REQUIRED ─────  (future) CUGA paused waiting for human input
```

---

## Why the proxy layer matters

The UI is served from port 8002 (feed-watcher). Without a proxy, the browser would try to call port 8001 directly — a different origin — and CORS blocks it. The feed-watcher service proxies all `/runtime/*` calls to cuga-runtime, so the browser only ever talks to one origin.

```
  Browser  ──── /fetch   ──────────────────────► Feed Watcher (8002)
           ──── /runtime/events ────────────────► Feed Watcher (8002) ──► cuga-runtime (8001)
           ──── /runtime/events?limit=200 ───────► Feed Watcher (8002) ──► cuga-runtime (8001)
           ──── /runtime/health ────────────────► Feed Watcher (8002) ──► cuga-runtime (8001)
```

---

## How context becomes a CUGA query

The worker's `_build_query()` folds the `context` dict into plain text appended to the `query` string. CUGA core receives one clean natural-language string — no schema changes, no new endpoints.

**What the adapter sends:**
```json
{
  "query": "You are monitoring AI blogs for signal about agentic AI. Given the post below, decide: 1. Is this relevant?…",
  "trigger": "rss.new_post.anthropic",
  "context": {
    "feed_name": "Anthropic Blog",
    "title": "Claude can now use tools in parallel",
    "text": "We've updated Claude to support parallel tool use…",
    "url": "https://www.anthropic.com/blog/tool-use",
    "posted_at": "2025-04-10T14:00:00Z"
  }
}
```

**What CUGA receives (one string):**
```
You are monitoring AI blogs for signal about agentic AI. Given the post below, decide:
1. Is this relevant to agentic AI? (yes/no)
…

Context:
  feed_name: Anthropic Blog
  title: Claude can now use tools in parallel
  text: We've updated Claude to support parallel tool use…
  url: https://www.anthropic.com/blog/tool-use
  posted_at: 2025-04-10T14:00:00Z
```

**What CUGA streams back (SSE):**
```
event: Answer
data: {"data": "relevant: yes\nsignal: high\nsummary: Anthropic adds parallel tool use, directly enabling more capable agentic pipelines", "variables": {}}
```

The `client.py` `_extract_text()` helper unwraps the JSON envelope and returns the inner `data` string for parsing.

---

## Module responsibilities

```
  ┌─────────────┐
  │  schemas.py │  Data shapes only. No logic. No imports from this package.
  └──────┬──────┘
         │ imported by
  ┌──────▼──────┐
  │   queue.py  │  Stores tasks + results. asyncio.Queue + dict. No HTTP.
  └──────┬──────┘
         │ imported by
  ┌──────▼──────┐     ┌────────────┐
  │   app.py    │     │  worker.py │  Background asyncio task. Orchestrates queue → client.
  │  (FastAPI)  │     └─────┬──────┘
  └─────────────┘           │ calls
                      ┌─────▼──────┐
                      │  client.py │  Pure async HTTP. Calls CUGA /stream. Parses SSE.
                      └────────────┘
```

Dependency direction is strictly downward. `client.py` is a leaf — it has no internal dependencies and can be tested in isolation.

---

## API reference

### `POST /events` — submit a task

Returns 202 immediately. Never waits for CUGA.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `query` | string | **yes** | The standing instruction for CUGA (the assessment prompt) |
| `persona` | string | no | CUGA persona to use. Default: `"default"` |
| `trigger` | string | no | Label for what caused this. Default: `"manual"` |
| `context` | object | no | Key-value data folded into the query |
| `callback_url` | string | no | POST result here when done instead of polling |
| `thread_id` | string | no | Continue an existing CUGA conversation |
| `task_id` | string | no | Supply your own ID; auto-generated if omitted |

```json
// Response
{ "task_id": "a1b2c3-…", "status": "queued" }
```

---

### `GET /events` — list all events

```
GET /events?status=completed&trigger=rss.new_post.anthropic&limit=20
```

| Query param | Effect |
|-------------|--------|
| `status` | Filter: `queued` / `running` / `completed` / `failed` |
| `trigger` | Filter by trigger name |
| `limit` | Max results (1–500, default 50) |

```json
// Response
{
  "counts": { "queued": 0, "running": 1, "completed": 14, "failed": 2 },
  "events": [ { "task_id": "…", "status": "completed", "trigger": "…", "output": "…" } ]
}
```

---

### `GET /events/{task_id}` — get one event

Returns the merged task (what was submitted + current result).

| Field | When present |
|-------|-------------|
| `status` | Always |
| `output` | When `completed` |
| `error` | When `failed` |
| `thread_id` | After CUGA processes it |
| `completed_at` | ISO-8601, on `completed` or `failed` |

**404** if the `task_id` was never submitted (or the process restarted).

---

### `GET /health` — liveness check

```json
{
  "status": "ok",
  "queue_depth": 3,
  "total_events_seen": 47,
  "cuga_url": "http://localhost:7860"
}
```

Watch `queue_depth` for backpressure — if it keeps growing, CUGA is slower than the ingest rate.

---

## Phase 1 trade-offs

| Trade-off | Phase 1 choice | Acceptable because | Phase 2 fix |
|-----------|---------------|-------------------|-------------|
| In-memory queue | `asyncio.Queue` | Tasks lost on restart — fine for demos and single-session use | Swap `queue.py` for Postgres `SELECT FOR UPDATE SKIP LOCKED` |
| Single worker | One coroutine | Enough for < ~10 concurrent CUGA calls | Run multiple `TaskWorker` instances against shared queue |
| No retry | Fail on first error | Fail-fast is easier to debug | Add retry + exponential backoff in `worker._process()` |
| No auth | Open `/events` endpoint | Internal / dev use only | Add API-key header check as a FastAPI dependency |
| No persistence | Results lost on restart | Acceptable for live dashboards | Postgres-backed results in Phase 2 |

---

## What changes in Phase 2

The entire runtime upgrade is a single file swap:

```
Phase 1 (now)                         Phase 2 (next)
────────────────────────────────       ────────────────────────────────
queue.py:  asyncio.Queue               queue.py:  Postgres cuga_tasks table
           + in-memory dict                       SELECT FOR UPDATE SKIP LOCKED
                                                  (workers can run on multiple hosts)

worker.py:  unchanged
client.py:  unchanged
app.py:     unchanged
schemas.py: unchanged
```
