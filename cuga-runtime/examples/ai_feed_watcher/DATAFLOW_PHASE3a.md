# Phase 3a Data Flow — Feed Watcher with Kafka Streaming

> Phase 3a is one change: replace the Postgres queue with a Kafka topic. Everything else —
> the feed watcher, CUGA, the result store — stays identical. But that one change restructures
> how fault recovery, scaling, and replay work.

---

## The Full Picture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  14 RSS / Atom Feeds                                                      ║
║  openai · anthropic · deepmind · huggingface · the_batch · lilianweng   ║
║  simonwillison · import_ai · mollick · interconnects · bair · aisnakeoil ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  APScheduler fires every 15 min
                           │  feed_watcher.py fetches all feeds in parallel
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Feed Watcher Service  (port 8002)                                        ║
║                                                                           ║
║  for each new post (not in seen_posts):                                   ║
║    producer.produce(                                                      ║
║      topic = "cuga.tasks.ai_feed",                                       ║
║      key   = post["source"],          ← "anthropic", "openai", etc.      ║
║      value = CugaTaskEvent JSON,                                          ║
║    )                                                                      ║
║    INSERT INTO seen_posts (post_id, source, first_seen_at)               ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  acks=all  (broker confirms write before flush)
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Kafka  —  topic: cuga.tasks.ai_feed  (3 partitions, 7-day retention)    ║
║                                                                           ║
║  Partition 0  key: anthropic, openai, deepmind                           ║
║  Partition 1  key: huggingface, the_batch, lilianweng, simonwillison     ║
║  Partition 2  key: import_ai, mollick, interconnects, bair, aisnakeoil  ║
║                                                                           ║
║  Each message:                                                            ║
║    offset  →  immutable position in the log                              ║
║    key     →  determines partition (same source = same partition)        ║
║    value   →  CugaTaskEvent JSON                                         ║
╚═════════════╤════════════════╤════════════════════════════════════════════╝
              │                │                 (one worker per partition max)
              ▼                ▼
     ┌──────────────┐ ┌──────────────┐
     │  Worker 1    │ │  Worker 2    │   consumer group: cuga-runtime-workers
     │              │ │              │
     │  poll()      │ │  poll()      │   ← offset NOT committed yet
     │  → CUGA      │ │  → CUGA      │   ← CUGA does the work
     │  commit()    │ │  commit()    │   ← offset committed ONLY on success
     └──────┬───────┘ └──────┬───────┘
            └────────┬───────┘
                     │  HTTP POST /stream
                     ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)                                                        ║
║  receives a normal REST call — no Kafka awareness                         ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │
           ┌───────────────┼───────────────────┐
           ▼               ▼                   ▼
  cuga.tasks.results   cuga.tasks.dlq     Browser UI
  (assessment output)  (failed posts,     polls GET /runtime/events
                        after 3 retries)
```

---

## Step-by-Step Data Flow

### Step 1 — Scheduler fires

APScheduler triggers `poll_all_feeds()` every 15 minutes (configurable via `FEED_POLL_INTERVAL_MIN`).

```python
async def poll_all_feeds():
    cutoff = datetime.now(UTC) - timedelta(days=7)
    posts, _ = await _fetch_rss_feeds(RSS_FEEDS, cutoff)
    # posts = list of dicts: {post_id, source, title, text, url, posted_at}
```

All 14 feeds are fetched in parallel with `asyncio.gather`. The `cutoff` prevents re-processing old history on first run.

---

### Step 2 — Deduplicate

For each post, check `seen_posts` (SQLite or Postgres):

```python
for p in posts:
    if await _already_seen(p["post_id"]):
        continue                              # skip — already processed this post
    await _mark_seen(p["post_id"], p["source"])
```

`seen_posts` schema:
```sql
CREATE TABLE seen_posts (
    post_id     TEXT PRIMARY KEY,
    feed_source TEXT NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`post_id` is derived from the post URL or GUID — stable across fetches. The same post from `openai` will always produce the same `post_id`, so even if the scheduler fires twice before the first run completes, no duplicates enter Kafka.

---

### Step 3 — Produce to Kafka

For each new (unseen) post:

```python
producer.produce(
    topic="cuga.tasks.ai_feed",
    key=p["source"],                        # "anthropic", "openai", "deepmind", etc.
    value=json.dumps({
        "task_id":  str(uuid.uuid4()),
        "query":    AI_RELEVANCE_PROMPT,    # the assessment question
        "trigger":  f"rss.new_post.{p['source']}",
        "context": {
            "feed_name":  p["name"],
            "title":      p["title"],
            "text":       p["text"],
            "url":        p["url"],
            "posted_at":  p["posted_at"],
            "source":     p["source"],
        },
    }),
)
producer.flush()  # wait for broker acknowledgement before continuing
```

**Why `key=p["source"]`:**
Kafka hashes the key to determine partition. All posts from `anthropic` always land in the same partition. This means:
- Posts from a single feed are processed in order
- One worker "owns" each feed — no interleaving between feeds mid-batch
- Consistent partition routing enables per-feed consumer lag monitoring

**Why `acks=all`:**
The producer waits for the Kafka broker to confirm the write is replicated before `flush()` returns. The feed watcher only inserts into `seen_posts` after the produce succeeds. If the broker is unavailable, the post stays out of `seen_posts` and will be retried on the next scheduler tick.

---

### Step 4 — Kafka stores the message

The message sits in the topic as an immutable log entry:

```
cuga.tasks.ai_feed  Partition 0
┌────────┬───────────┬─────────────────────────────────────────────────────┐
│ Offset │ Key       │ Value (CugaTaskEvent)                                │
├────────┼───────────┼─────────────────────────────────────────────────────┤
│   0    │ anthropic │ {task_id: "a1b2...", query: "You are monitoring..."} │
│   1    │ openai    │ {task_id: "c3d4...", query: "You are monitoring..."} │
│   2    │ anthropic │ {task_id: "e5f6...", query: "You are monitoring..."} │
│   3    │ deepmind  │ {task_id: "g7h8...", query: "You are monitoring..."} │
└────────┴───────────┴─────────────────────────────────────────────────────┘
                                          ▲
                              consumer group offset:
                              "cuga-runtime-workers has read up to offset 1"
                              "offset 2 and 3 are pending"
```

The message stays in the topic for 7 days regardless of whether it's been consumed. The offset is the only state the consumer group tracks — not a row status, not a lock.

---

### Step 5 — Worker polls and claims

`CugaKafkaWorker` runs a poll loop:

```python
while True:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        continue

    event = json.loads(msg.value())
    # offset is NOT committed here — the message is "in flight"
```

**How this differs from Postgres `SELECT FOR UPDATE SKIP LOCKED`:**

| | Postgres (Phase 2) | Kafka (Phase 3a) |
|---|---|---|
| Claim mechanism | Row lock | Partition assignment |
| Two workers same task? | Prevented by lock | Prevented by partition ownership |
| Crash mid-task | Row stuck `running` until cleanup job | Offset uncommitted → replays automatically |
| Scale out | Add workers, they compete for rows | Add workers, Kafka rebalances partitions |
| Scale in | Remove workers, no cleanup needed | Remove workers, Kafka rebalances partitions |

At most one worker is assigned to each partition. Kafka's consumer group protocol handles assignment automatically. No application-level coordination required.

---

### Step 6 — Worker calls CUGA

```python
result = await cuga_client.stream(
    query=event["query"],
    context=event["context"],
    thread_id=event.get("thread_id"),
)
```

CUGA receives a normal HTTP POST to `/stream`. It has no awareness of Kafka. The response streams back as SSE. The worker buffers the full response.

---

### Step 7 — Commit offset (only on success)

```python
if result.success:
    # publish result to output topic
    result_producer.produce(
        topic="cuga.tasks.results",
        key=event["task_id"],
        value=json.dumps({
            "task_id": event["task_id"],
            "trigger": event["trigger"],
            "output":  result.text,
            "context": event["context"],
        }),
    )
    # NOW commit — this is the fault safety guarantee
    consumer.commit(msg)

else:
    retry_count = event.get("_retry", 0) + 1
    if retry_count < MAX_RETRIES:
        # re-produce with incremented retry count
        producer.produce("cuga.tasks.ai_feed", ...)
        consumer.commit(msg)   # commit original, retry is a new message
    else:
        producer.produce("cuga.tasks.dlq", ...)
        consumer.commit(msg)
```

**The commit-after-success guarantee:**
The offset is the consumer group's bookmark. Committing offset N means "I have successfully processed everything up to N." If the worker commits before CUGA returns and then crashes, that event is gone. By committing only after CUGA succeeds, a crash mid-call means the offset is never advanced — the event replays on the next poll after restart.

---

### Step 8 — Result lands in output topic

```
cuga.tasks.results  Partition 0
┌────────┬────────────┬──────────────────────────────────────────────────────────┐
│ Offset │ Key        │ Value                                                     │
├────────┼────────────┼──────────────────────────────────────────────────────────┤
│   0    │ a1b2...    │ {task_id, trigger: "rss.new_post.anthropic",             │
│        │            │  output: "relevant: yes\nsignal: high\nsummary: ..."}    │
│   1    │ c3d4...    │ {task_id, trigger: "rss.new_post.openai",                │
│        │            │  output: "relevant: no\nreason: benchmark"}              │
└────────┴────────────┴──────────────────────────────────────────────────────────┘
```

The Browser UI polls `GET /runtime/events?limit=200` via the feed watcher proxy. The runtime service reads from `cuga.tasks.results` and serves the results as JSON.

---

## Fault Scenarios

### Scenario A — Worker crashes mid-assessment

```
Worker 1 polls offset 5 (Anthropic post)
Worker 1 calls CUGA /stream
Worker 1 process killed at this point
  → offset 5 never committed
  → consumer group still shows "last committed: offset 4"

Worker 1 restarts (or Worker 2 picks up Partition 0 after rebalance)
  → polls from offset 5 again
  → calls CUGA /stream again
  → commits offset 5 on success
```

No message lost. No cleanup job needed. No row stuck in `running` state.

---

### Scenario B — CUGA returns an error

```
Worker 1 polls offset 7 (Lilian Weng post)
Worker 1 calls CUGA /stream
CUGA returns 500

Worker 1:
  retry_count = 0 + 1 = 1
  re-produce to cuga.tasks.ai_feed with _retry=1
  commit offset 7   ← original message acknowledged, retry is a new message

(2 retries later, still failing)
  produce to cuga.tasks.dlq
  commit offset 9

DLQ consumer (human or automated):
  reads from cuga.tasks.dlq
  decides: fix CUGA config, then replay from DLQ
```

---

### Scenario C — Replay last week with a new prompt

```
1. Edit the AI_RELEVANCE_PROMPT constant (Phase 3a)
   (or edit rss_post.j2 template in Phase 3b)

2. Reset consumer group offset to 7 days ago:
   kafka-consumer-groups.sh \
     --reset-offsets \
     --to-datetime 2026-04-07T00:00:00.000 \
     --execute

3. Restart workers
   → they re-process every event from offset 0 of that window
   → feed watcher continues normally, adding new posts to the same topic
   → old results in cuga.tasks.results are overwritten by new scores

No re-fetching RSS. No re-running the feed watcher. No duplicates in seen_posts.
```

---

## Message Schema

`CugaTaskEvent` (produced to `cuga.tasks.ai_feed`):

```json
{
  "task_id":  "550e8400-e29b-41d4-a716-446655440000",
  "query":    "You are monitoring AI research blogs for signal about agentic AI...",
  "trigger":  "rss.new_post.anthropic",
  "context": {
    "feed_name":  "Anthropic Blog",
    "title":      "Claude now supports parallel tool calls",
    "text":       "Today we're releasing...",
    "url":        "https://www.anthropic.com/...",
    "posted_at":  "2026-04-14T10:00:00Z",
    "source":     "anthropic"
  }
}
```

`CugaResultEvent` (produced to `cuga.tasks.results`):

```json
{
  "task_id":   "550e8400-e29b-41d4-a716-446655440000",
  "trigger":   "rss.new_post.anthropic",
  "output":    "relevant: yes\nsignal: high\nsummary: Claude gains parallel tool execution...",
  "context":   { ...same as above... },
  "completed_at": "2026-04-14T10:00:04.820Z"
}
```

Both schemas are identical to Phase 2. Only the transport changed.
