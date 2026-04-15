# How the Queue Evolves: Phase 1 → Phase 2 → Phase 3

> The same RSS post flows through a fundamentally different infrastructure at each phase.
> The event schema never changes. CUGA never changes. Only how the task gets from
> "new post found" to "CUGA processes it" changes — and that changes everything.

---

## The Core Problem Each Phase Solves

| Phase | Core problem | Solution |
|---|---|---|
| 1 | I want to see CUGA work | asyncio.Queue in memory |
| 2 | Tasks get lost, I have to click, can't scale | Postgres-backed durable queue |
| 3a | I want fault recovery, replay, and horizontal scale without coordination overhead | Kafka topic with consumer group |

---

## Phase 1 — In-Memory Queue

### What the queue is

```python
# queue.py (Phase 1)
task_queue: asyncio.Queue = asyncio.Queue()
results: dict[str, TaskResult] = {}
```

Two Python objects. Alive only while the process is running.

### How a post flows through it

```
User clicks "Fetch Feeds"
  → service.py fetches RSS
  → for each new post: await task_queue.put(CugaTaskEvent)
  → returns list of posts to UI

User clicks "Analyze All" (or per-card "Analyze")
  → POST /events  { query, trigger, context }
  → service.py: await task_queue.put(event)
  → returns 202 + task_id

Single worker loop:
  while True:
      event = await task_queue.get()
      result = await cuga_client.stream(event.query, event.context)
      results[event.task_id] = result
      task_queue.task_done()

UI polls GET /events/{task_id} until status == "completed"
```

### What happens when things go wrong

```
Process restarts:
  task_queue = []    ← empty
  results = {}       ← empty
  Everything gone.

CUGA returns 500:
  Exception propagates up
  Worker loop crashes
  All remaining queued tasks: gone

Two instances running:
  Each has its own queue
  POST /events hits instance A
  Worker on instance B sees nothing
  Both instances process some tasks, neither has all results
```

### The picture

```
Browser
  │  POST /events
  ▼
FastAPI ──► asyncio.Queue ──► Single Worker ──► CUGA
              (memory)
              ↑ gone on restart
```

### What Phase 1 is good for

Proving the CUGA assessment loop works. Showing the idea to someone. Nothing more.

---

## Phase 2 — Postgres Queue

### What the queue is

```sql
CREATE TABLE cuga_tasks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','completed','failed')),
    query         TEXT NOT NULL,
    trigger       TEXT NOT NULL DEFAULT 'manual',
    context       JSONB NOT NULL DEFAULT '{}',
    output        TEXT,
    error         TEXT,
    retry_count   INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX idx_cuga_tasks_queued ON cuga_tasks (created_at)
    WHERE status = 'queued';
```

A Postgres table. Alive as long as the database is alive.

### How a post flows through it

```
APScheduler fires every 30 min (no human needed)
  → service.py fetches all 14 RSS feeds
  → for each new post:
      INSERT INTO seen_posts (post_id, source, ...)
      POST /events to cuga-runtime
        → INSERT INTO cuga_tasks (status='queued', query=..., context=...)
        → return 202 + task_id

Worker loop (N workers, each in its own process):
  while True:
      BEGIN;
      UPDATE cuga_tasks
        SET status = 'running'
        WHERE id = (
          SELECT id FROM cuga_tasks
          WHERE status = 'queued'
            AND (next_retry_at IS NULL OR next_retry_at <= NOW())
          ORDER BY created_at
          LIMIT 1
          FOR UPDATE SKIP LOCKED    ← key: other workers skip locked rows
        )
      RETURNING *;
      COMMIT;

      if no row returned: sleep 500ms, continue

      result = await cuga_client.stream(task.query, task.context)

      if success:
          UPDATE cuga_tasks SET status='completed', output=result WHERE id=task.id
      else:
          if task.retry_count < 4:
              UPDATE cuga_tasks
                SET status='queued',
                    retry_count = retry_count + 1,
                    next_retry_at = NOW() + interval '1s' * power(2, retry_count)
              -- 1s, 2s, 4s, 8s, then permanent failure
          else:
              UPDATE cuga_tasks SET status='failed', error=... WHERE id=task.id

UI polls GET /events/{task_id}
  → SELECT * FROM cuga_tasks WHERE id = $1
```

### How N workers avoid stepping on each other

`FOR UPDATE SKIP LOCKED` is the key. When Worker 1 locks a row, Worker 2's query physically skips that row and finds the next unlocked one. No application-level coordination. No leader election. Postgres handles it.

```
cuga_tasks table:
┌──────────────┬─────────┐
│ id           │ status  │
├──────────────┼─────────┤
│ a1b2         │ running │  ← Worker 1 has locked this row
│ c3d4         │ running │  ← Worker 2 has locked this row
│ e5f6         │ queued  │  ← Worker 3 can claim this
│ g7h8         │ queued  │  ← Worker 4 can claim this
└──────────────┴─────────┘
```

### What happens when things go wrong

```
Worker crashes mid-task:
  Row stays status='running'
  No other worker picks it up (it looks in-progress)
  Needs a cleanup job: UPDATE ... SET status='queued' WHERE status='running'
  AND updated_at < NOW() - interval '10 minutes'

Postgres goes down:
  Workers can't poll → they wait
  Feed watcher can't INSERT → new posts queue up until DB returns
  No data lost, but tasks stall

Two instances running:
  Both workers hit the same Postgres → SKIP LOCKED coordinates them correctly
  This actually works fine
```

### The picture

```
Feed Watcher (APScheduler)
  │  POST /events (every new post, automatically)
  ▼
FastAPI ──► INSERT INTO cuga_tasks ──► Postgres
                                          │
                    ┌─────────────────────┤
                    │  SELECT FOR UPDATE  │
                    │  SKIP LOCKED        │
                    ▼                     ▼
               Worker 1             Worker 2  …N
                    │
                    ▼
                  CUGA
```

### What Phase 2 adds over Phase 1

- Tasks survive restarts ✓
- Automation (no human trigger) ✓
- N workers without coordination ✓
- Retry with exponential backoff ✓
- Full task history queryable via SQL ✓

### What Phase 2 can't do

- Stuck `running` rows need a cleanup job
- No replay — to re-score posts you must re-fetch and re-insert
- Prompt is hardcoded in Python; changing it requires a redeploy
- Scaling workers requires managing Postgres connection pool size
- No "stream the data" semantics — it's a pull-based polling loop

---

## Phase 3a — Kafka Consumer Group

### What the queue is

```
Topic: cuga.tasks.ai_feed
  Partitions: 3
  Retention:  7 days
  Key:        post.source  ("anthropic", "openai", etc.)

Topic: cuga.tasks.results
Topic: cuga.tasks.dlq
```

An append-only log, partitioned across brokers. Not a table. Not a queue in the traditional sense.

### How a post flows through it

```
APScheduler fires every 15 min
  → service.py fetches all 14 RSS feeds
  → for each new post:
      INSERT INTO seen_posts (post_id, source, ...)
      producer.produce(
        topic = "cuga.tasks.ai_feed",
        key   = post["source"],       ← "anthropic" → always Partition 0
        value = CugaTaskEvent JSON,
      )
      producer.flush()                ← wait for broker ack

Kafka stores message as immutable log entry:
  Partition 0, Offset 42:
    key=anthropic, value={task_id, query, context}

CugaKafkaWorker (consumer group: cuga-runtime-workers):
  Assigned to Partition 0 by Kafka coordinator
  consumer.poll(timeout=1.0)
    → returns message at offset 42
    → offset NOT committed yet

  result = await cuga_client.stream(event["query"], event["context"])

  if success:
    result_producer.produce("cuga.tasks.results", value=...)
    consumer.commit(msg)    ← NOW advance offset to 43
                              "I have processed everything through offset 42"
  else:
    if retry_count < 3:
      re-produce to cuga.tasks.ai_feed with _retry++ (new message, new offset)
      consumer.commit(msg)  ← commit original; retry is separate
    else:
      produce to cuga.tasks.dlq
      consumer.commit(msg)
```

### How N workers avoid stepping on each other

Kafka assigns each partition to exactly one consumer in the group. There is no possibility of two workers processing the same message — it's prevented at the protocol level, not the application level.

```
cuga.tasks.ai_feed:
  Partition 0  →  Worker 1  (anthropic, openai, deepmind)
  Partition 1  →  Worker 2  (huggingface, the_batch, lilianweng, simonwillison)
  Partition 2  →  Worker 3  (import_ai, mollick, interconnects, bair, aisnakeoil)

Worker 4 joins consumer group:
  Kafka rebalances automatically
  Partition 2 moves from Worker 3 to Worker 4
  No application code involved

Worker 1 crashes:
  Kafka detects heartbeat timeout (session.timeout.ms)
  Rebalances: Partition 0 reassigns to Worker 2 or a new worker
  Worker 2 polls from last committed offset on Partition 0
  Message Worker 1 was processing (never committed) → replays automatically
```

### What happens when things go wrong

```
Worker crashes mid-assessment:
  Offset never committed
  Next poll from any worker on that partition starts at last committed offset
  Message replays — no cleanup job, no stuck rows, no manual intervention

CUGA returns 500:
  Worker re-produces to cuga.tasks.ai_feed with _retry=1 (new message)
  Commits original offset
  Worker picks up the retry message on next poll
  After 3 retries: routes to cuga.tasks.dlq

Kafka broker goes down (single broker):
  Producer blocks until broker returns (acks=all)
  Consumer workers pause
  No data lost — Kafka log is durable
  When broker returns: workers resume from last committed offset

Want to replay last week with a new prompt:
  kafka-consumer-groups.sh --reset-offsets --to-datetime 2026-04-07T00:00:00.000
  Restart workers
  Every message from that window re-processed through the current prompt
  No re-fetching RSS. No touching seen_posts. No duplicates.
```

### The picture

```
Feed Watcher (APScheduler)
  │  producer.produce()  (every new post, automatically)
  ▼
Kafka  cuga.tasks.ai_feed
  Partition 0 ─────────────► Worker 1 ─► CUGA
  Partition 1 ─────────────► Worker 2 ─► CUGA
  Partition 2 ─────────────► Worker 3 ─► CUGA
                                │
                                ▼
                         cuga.tasks.results
                         (immutable output log)
```

---

## Side-by-Side Comparison

### The claim mechanism

```
Phase 1:  asyncio.Queue.get()
          → one coroutine gets the item, others don't
          → works only within a single process

Phase 2:  SELECT id FROM cuga_tasks WHERE status='queued'
          FOR UPDATE SKIP LOCKED
          → Postgres locks the row
          → other workers skip it
          → works across processes and hosts

Phase 3a: Kafka partition assignment
          → each partition owned by exactly one consumer in the group
          → no locking, no row state, no polling interval
          → works across processes, hosts, and data centers
```

### Fault recovery

```
Phase 1:  crash → all tasks gone
          fix:  there is no fix

Phase 2:  crash → row stays 'running'
          fix:  cleanup job marks stuck rows back to 'queued'
          gap:  rows can be stuck for minutes until cleanup fires

Phase 3a: crash → offset never committed
          fix:  nothing — next poll from last committed offset is automatic
          gap:  none (unless commit happened before CUGA returned, which it doesn't)
```

### Replay

```
Phase 1:  no replay
          to re-score a post: click "Analyze" again

Phase 2:  no native replay
          to re-score a post: UPDATE cuga_tasks SET status='queued', retry_count=0
          to re-score a week: complex — delete results, re-queue in bulk

Phase 3a: native replay
          to re-score any historical window:
            kafka-consumer-groups.sh --reset-offsets --to-datetime <timestamp>
          RSS is never re-fetched. seen_posts is unchanged.
```

### Scaling

```
Phase 1:  1 worker, hardcoded
          to add workers: not supported

Phase 2:  N workers, each polls Postgres
          to add workers: start new process with CUGA_DB_URL
          constraint: connection pool (100 workers = 100 Postgres connections)

Phase 3a: N workers, Kafka assigns partitions
          to add workers: start new process with CUGA_KAFKA_BOOTSTRAP
          constraint: topic partitions (3 partitions = max 3 concurrent workers)
          to increase: add partitions to the topic (one-time operation)
```

### Prompt changes

```
Phase 1:  hardcoded Python constant
          to change: edit code, restart service

Phase 2:  hardcoded Python constant
          to change: edit code, restart service, re-queue old posts manually

Phase 3a: hardcoded Python constant
          to change: edit code, restart service
          advantage: replay re-runs old posts through new prompt automatically

Phase 3b: Jinja2 template file (next phase)
          to change: edit template file, reset offset, restart workers
          no code change, no service restart
```

---

## What Each Phase Looks Like When a Worker Dies Mid-Task

This is the clearest way to see the fundamental difference.

**Phase 1:**
```
Worker is processing post #7 (Lilian Weng)
Process killed
Post #7: gone from queue, no result, no trace
Posts #8-14: gone from queue
```

**Phase 2:**
```
Worker claimed post #7 → row is status='running'
Process killed
Post #7: stuck in 'running' state
Nobody processes it until cleanup job fires (e.g., every 5 minutes)
Posts #8-14: still in queue as 'queued', other workers can pick them up
```

**Phase 3a:**
```
Worker polled post #7 at offset 42 → offset NOT committed
Process killed
Post #7: Kafka still shows consumer group at offset 41
Next worker assigned to that partition starts polling from offset 42
Post #7 replays immediately on the next poll()
Posts #8-14: also waiting in the topic, unaffected
```

Phase 3a is the only phase where the system recovers itself, instantly, without any cleanup infrastructure.
