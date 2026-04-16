# AI Feed Watcher — Phase 2

> **What changes:** The feed watcher becomes a scheduled background service, not a manual-fetch UI tool. Feeds are polled on a cron schedule. Results persist across restarts. The UI is always live — it shows the full history, not just the current session.

Phase 1 requires someone to open a browser, click "Fetch Feeds," and click "Analyze." Phase 2 makes the whole pipeline automatic: feeds are checked on a schedule, posts are queued continuously, and the UI is a read-only dashboard that reflects persistent state.

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  RSS / Atom Feeds                                                            ║
╚══════════════════════════╤═══════════════════════════════════════════════════╝
                           │  scheduled fetch (every 30 min via APScheduler)
                           ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  Feed Watcher Service  (Phase 2)                                             ║
║                                                                              ║
║  GET  /                 Serve the UI                                         ║
║  GET  /runtime/*        Proxy to cuga-runtime (same as Phase 1)             ║
║                                                                              ║
║  Background scheduler (APScheduler):                                         ║
║    Every 30 min → fetch all feeds → compare against seen_posts table        ║
║    New post found → POST /events to cuga-runtime immediately                ║
║                                                                              ║
║  Seen-posts store (SQLite or Postgres):                                      ║
║    post_id · feed_source · first_seen_at                                    ║
║    Prevents re-queueing the same post across restarts or re-fetches         ║
╚══════════════════════════╤═══════════════════════════════════════════════════╝
                           │  POST /events  { query, trigger, context }
                           │  (automatically, no UI click needed)
                           ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  cuga-runtime  (Phase 2)                                                     ║
║                                                                              ║
║  Postgres-backed queue. Tasks survive restarts.                              ║
║  N workers. Retry with backoff. API key auth.                                ║
╚══════════════════════════╤═══════════════════════════════════════════════════╝
                           │  POST /stream
                           ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)                                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
                           │
                           ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║  Browser UI  (served from port 8002)                                         ║
║                                                                              ║
║  Now a read-only dashboard. No "Fetch Feeds" or "Analyze" buttons.          ║
║  Shows all completed assessments from the Postgres result store.            ║
║  Refreshes automatically. Filters by feed, signal, date range.              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Data flow — automatic pipeline

```
1. Scheduler fires (every 30 min, or configurable)
   → fetch all 14 RSS/Atom feeds in parallel
   → for each post: check seen_posts table
   → new post (not in seen_posts):
       → INSERT into seen_posts (deduplicated forever)
       → POST /events to cuga-runtime with X-API-Key
       → cuga-runtime returns 202 immediately

2. cuga-runtime worker (runs independently, N instances)
   → pulls task from Postgres queue
   → calls CUGA /stream
   → writes result back to Postgres (status=completed, output=…)
   → if CUGA fails: retry with exponential backoff (up to 4 attempts)
   → if all retries exhausted: status=failed, logged to DLQ

3. UI (browser, read-only dashboard)
   → polls GET /runtime/events?limit=200 every 10s
   → renders all completed assessments from cuga-runtime result store
   → no manual interaction needed to see new results
   → history persists across page reloads (it's in Postgres)
```

---

## What changes in the codebase

### `service.py` — new background scheduler

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(
        poll_all_feeds,
        trigger="interval",
        minutes=int(os.getenv("FEED_POLL_INTERVAL_MIN", "30")),
    )
    scheduler.start()

async def poll_all_feeds():
    """Fetch all feeds, deduplicate, submit new posts to cuga-runtime."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    posts, _ = await _fetch_rss_feeds(RSS_FEEDS, cutoff)

    async with httpx.AsyncClient() as http:
        for p in posts:
            if await _already_seen(p["post_id"]):   # SQLite/Postgres check
                continue
            await _mark_seen(p["post_id"], p["source"])
            await http.post(
                f"{_RUNTIME_URL}/events",
                json={
                    "query":   AI_RELEVANCE_PROMPT,
                    "trigger": f"rss.new_post.{p['source']}",
                    "context": { ...p fields... },
                },
                headers={"X-API-Key": os.getenv("CUGA_API_KEY")},
            )
```

### `seen_posts.py` — deduplication store

```python
# SQLite for single-host, Postgres for multi-host deployment

CREATE TABLE seen_posts (
    post_id     TEXT PRIMARY KEY,
    feed_source TEXT NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `ui/index.html` — read-only dashboard

- Remove "Fetch Feeds" and "Analyze" buttons
- Remove `fetchedPosts` state and per-card "Analyze" button
- Keep feed sidebar, signal filter, tab bar
- Poll every 10s instead of 4s (no pending local state to show quickly)
- Add date-range filter (today / last 7 days / last 30 days)
- Stats bar shows all-time counts from Postgres, not session counts

---

## Running it

```bash
# 1. Start CUGA
# 2. Start Postgres (or use SQLite for local dev)

# 3. Start cuga-runtime (Phase 2 — Postgres-backed)
CUGA_DB_URL=postgresql://localhost/cuga \
CUGA_API_KEY=secret \
CUGA_API_URL=http://localhost:7860 \
  uvicorn cuga_runtime.app:app --port 8001

# 4. Start the feed watcher service
CUGA_RUNTIME_URL=http://localhost:8001 \
CUGA_API_KEY=secret \
FEED_POLL_INTERVAL_MIN=30 \
  uvicorn examples.ai_feed_watcher.service:app --port 8002

# 5. Open the dashboard — it self-updates, no clicks needed
open http://localhost:8002
```

---

## New configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CUGA_RUNTIME_URL` | `http://localhost:8001` | cuga-runtime URL |
| `CUGA_API_KEY` | — | API key for cuga-runtime (Phase 2 required) |
| `FEED_POLL_INTERVAL_MIN` | `30` | How often to check feeds |
| `SEEN_POSTS_DB_URL` | `sqlite:///seen_posts.db` | Where to store seen post IDs |
| `FEED_LOOKBACK_DAYS` | `7` | How far back to consider posts as new |

---

## What Phase 3 adds

Phase 2 is automatic but still push-notifies nothing — you have to check the dashboard. Phase 3 adds real-time push to Slack/email for high-signal posts, a HITL approval flow for ambiguous assessments, and priority routing so a breaking Anthropic announcement never queues behind routine blog posts.
