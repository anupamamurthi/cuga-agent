# AI Feed Watcher — Phase 3

> **What changes:** The feed watcher becomes a Kafka producer. The cuga-runtime becomes a Kafka consumer group. Posts are never lost on crash. Any historical batch can be replayed through a new prompt without re-fetching RSS. The system plugs into the enterprise event mesh rather than running as a standalone HTTP service.

Phase 2 automates the pipeline with a Postgres queue. Phase 3 is more than a queue swap — the feed watcher becomes a first-class event source in the CUGA Streaming Runtime, with domain-specific routing, an EventTranslator, live knowledge updates, and a full audit trail.

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  RSS / Atom Feeds (14 sources, publicly accessible)                      ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  scheduled fetch (every 15 min)
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Feed Watcher Service  (Phase 3)                                         ║
║                                                                          ║
║  1. Fetch & parse RSS/Atom feeds                                         ║
║  2. Deduplicate against seen_posts store                                 ║
║  3. For each new post:                                                   ║
║     EventTranslator renders rss_post.j2 template                        ║
║     → Kafka produce to cuga.tasks.ai_feed                               ║
║       key:   post.source  (consistent partition per feed)               ║
║       value: CugaTaskEvent JSON                                          ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  acks=all, idempotent producer
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Kafka  —  topic: cuga.tasks.ai_feed                                     ║
║                                                                          ║
║  Partition 0:  anthropic, openai, deepmind  (high-priority feeds)       ║
║  Partition 1:  huggingface, the_batch, lilianweng, simonwillison        ║
║  Partition 2:  import_ai, mollick, interconnects, bair, aisnakeoil, …  ║
║                                                                          ║
║  Retention: 7 days  →  replay any historical window without re-fetching ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  consumer.poll()  — no polling delay
                           │  consumer group: cuga-runtime-workers
                           │
               ┌───────────┼──────────┐
               ▼           ▼          ▼
         ┌──────────┐ ┌──────────┐ ┌──────────┐
         │ Worker 1 │ │ Worker 2 │ │ Worker N │  … one per partition at max
         │ poll()   │ │ poll()   │ │ poll()   │
         │ → CUGA   │ │ → CUGA   │ │ → CUGA   │
         │ commit   │ │ commit   │ │ commit   │  ← only after CUGA success
         └────┬─────┘ └────┬─────┘ └────┬─────┘
              └────────────┴────────────┘
                           │  HTTP POST /stream
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)  — receives a normal REST call, no Kafka awareness    ║
╚══════════════════════════════════════════════════════════════════════════╝
                           │
              ┌────────────┼─────────────────────────┐
              ▼            ▼                          ▼
     cuga.tasks.results  cuga.audit.executions   cuga.tasks.dlq
     (assessment output) (full execution trace)  (failed posts)
              │
              ▼
     Browser UI  (polls GET /runtime/events via HTTP proxy)
```

---

## Pillar 1 — Stream: Kafka producer replaces HTTP POST

```python
# Phase 2: HTTP call to cuga-runtime
await http.post(
    f"{_RUNTIME_URL}/events",
    json={"query": AI_RELEVANCE_PROMPT, "trigger": ..., "context": ...},
    headers={"X-API-Key": os.getenv("CUGA_API_KEY")},
)

# Phase 3: Kafka produce — same CugaTaskEvent, different transport
producer.produce(
    topic="cuga.tasks.ai_feed",
    key=p["source"],                              # consistent partition per feed
    value=json.dumps({
        "task_id":  str(uuid.uuid4()),
        "query":    AI_RELEVANCE_PROMPT,
        "trigger":  f"rss.new_post.{p['source']}",
        "context":  { ...post fields... },
    }),
)
producer.flush()
```

The `CugaTaskEvent` schema is identical to Phase 1 and 2. Only the transport changes.

**Fault recovery:** If the feed watcher crashes mid-batch, already-produced messages stay in Kafka. Already-committed worker offsets are not re-processed. Only the message currently in-flight replays on restart. No duplicates, no lost posts.

---

## Pillar 2 — Connect: EventTranslator

Phase 3 introduces the EventTranslator — a Jinja2 template layer that converts structured RSS post data into the natural language prompt CUGA receives. This decouples the prompt from the feed watcher code: changing the assessment instructions means editing a template file, not deploying new service code.

```python
# runtime/event_translator.py
class EventTranslator:
    def translate(self, post: dict) -> str:
        template = self._load("ai_feed/rss_post.j2")
        return template.render(**post)
```

```jinja2
{# templates/ai_feed/rss_post.j2 #}
You are monitoring AI research blogs and newsletters for signal about agentic AI.
Given the post below, decide:
1. Is this relevant to agentic AI? (yes/no) — be strict: benchmarks alone are not agentic AI
2. If yes: one-sentence summary of the key insight
3. If yes: signal strength (high / medium / low) — high means new, actionable, or surprising
4. If no: one-word reason why not (e.g. benchmark, personal, unrelated)

Reply in this exact format:
relevant: yes|no
signal: high|medium|low   (omit if not relevant)
summary: <one sentence>   (omit if not relevant)
reason: <one word>        (omit if relevant)

Context:
  feed_name: {{ feed_name }}
  title: {{ title }}
  text: {{ text }}
  url: {{ url }}
  posted_at: {{ posted_at }}
```

CUGA receives a normal natural language prompt. It has no awareness that this came from an RSS feed or a Kafka topic.

**Why this matters for the feed watcher:** When you want to update the relevance criteria — stricter signal thresholds, different output format, additional dimensions — you edit the template and replay historical events through the new prompt. No service redeploy, no re-fetching RSS.

---

## Pillar 3 — Process: Streaming Knowledge for the feed watcher

Phase 3 adds an optional streaming knowledge pipeline that keeps CUGA's context about past AI developments continuously current. This makes assessments context-aware, not just post-aware.

```
Past AI blog posts → Kafka topic: enterprise.docs.ai_feed
        │
        ▼
StreamingKnowledgePipeline
  - chunk post content
  - generate embeddings
  - upsert into vector store
        │
        ▼
CUGA's knowledge_search tool
  (CUGA can now answer: "have we seen similar claims before?",
   "what was the prior coverage of this topic?")
```

Example of what this enables: when a new post claims "parallel tool use is now possible in production," CUGA's knowledge store already contains prior posts on the topic — enabling it to assess whether this is genuinely new or a known technique being reframed.

This is an optional Phase 3c addition. The core pipeline (3a, 3b) works without it.

---

## Pillar 4 — Govern: Audit trail for every assessment

With `KafkaActivityEmitter` registered at CUGA startup, every assessment produces an immutable record:

```
cuga.audit.executions topic:

{ thread_id, task_id,
  step_type: "llm_call",
  input: "You are monitoring AI blogs... Context: feed_name: Anthropic Blog...",
  output: "relevant: yes\nsignal: high\nsummary: ...",
  duration_ms: 4820,
  policy_applied: null,
  timestamp: "2026-04-14T10:23:45Z" }

{ thread_id, task_id,
  step_type: "final_answer",
  output: "relevant: yes\nsignal: high\nsummary: ...",
  ... }
```

Use cases for the feed watcher:
- **Reproduce any past assessment** — "why did the system mark this post as irrelevant on April 7?" → seek to that offset and read
- **Measure prompt quality** — compare assessment distributions before and after a prompt change using the audit log as ground truth
- **Compliance** — if the feed watcher influences business decisions (e.g. auto-drafting Slack posts for high-signal items), the audit trail is the evidence chain

---

## Data flow — one post, end to end

```
1. Scheduler fires (every 15 min)
   → fetch all 14 RSS/Atom feeds
   → check seen_posts table — skip already-processed post_ids
   → for each new post:
       EventTranslator renders rss_post.j2 with post data
       → produce to cuga.tasks.ai_feed, key=source
       → INSERT into seen_posts

2. Kafka Worker assigned to that partition
   → consumer.poll() — no polling delay
   → offset NOT yet committed
   → calls CUGA /stream: query (rendered prompt) + context
   → CUGA returns: relevant: yes / signal: high / summary: "..."

3. Worker publishes + commits
   → CugaResultEvent → cuga.tasks.results
   → AuditEvent → cuga.audit.executions (if emitter registered)
   → consumer.commit(msg)  ← NOW — fault-safe

4. Browser UI
   → polls GET /runtime/events?limit=200
   → card updates with green badge, signal strength, summary
```

---

## Event replay — the key Phase 3 capability

With Kafka retention set to 7 days, you can re-assess any historical window at any time.

**Scenario:** You've updated the Jinja2 template to be stricter about "high signal." You want to re-score all posts from the last week.

```bash
# 1. Reset consumer group offset to 7 days ago
kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --group cuga-runtime-workers \
  --topic cuga.tasks.ai_feed \
  --reset-offsets \
  --to-datetime 2026-04-07T00:00:00.000 \
  --execute

# 2. Restart workers — they re-process from that offset
# The feed watcher continues normally, adding new posts to the same topic
# Old results in the result store are overwritten by the replay
```

**Other replay scenarios:**
- CUGA was misconfigured and returned `relevant: no` for everything → fix config, replay
- New team member wants to understand the system's past signal quality → replay with detailed output mode
- Prompt A/B test → replay same batch through two different templates, compare distributions

---

## Running it

```bash
# 1. Start CUGA (port 7860)

# 2. Start Kafka
docker run -p 9092:9092 apache/kafka:3.7.0

# 3. Create topics
kafka-topics.sh --create --topic cuga.tasks.ai_feed   --partitions 3 --replication-factor 1 --bootstrap-server localhost:9092
kafka-topics.sh --create --topic cuga.tasks.results   --partitions 3 --replication-factor 1 --bootstrap-server localhost:9092
kafka-topics.sh --create --topic cuga.tasks.dlq       --partitions 1 --replication-factor 1 --bootstrap-server localhost:9092

# 4. Start cuga-runtime Kafka workers
CUGA_KAFKA_BOOTSTRAP=localhost:9092 \
CUGA_API_URL=http://localhost:7860 \
  python -m cuga_runtime.kafka_worker --topic cuga.tasks.ai_feed

# 5. Start the feed watcher service
CUGA_KAFKA_BOOTSTRAP=localhost:9092 \
FEED_POLL_INTERVAL_MIN=15 \
SEEN_POSTS_DB_URL=sqlite:///seen_posts.db \
  uvicorn examples.ai_feed_watcher.service:app --port 8002

# 6. Open the dashboard
open http://localhost:8002
```

---

## Files added in Phase 3

| File | What it does |
|------|-------------|
| `runtime/kafka_worker.py` | CugaKafkaWorker — consumer group, offset commit, DLQ |
| `runtime/event_translator.py` | Jinja2 template engine — structured event → CUGA prompt |
| `runtime/templates/ai_feed/rss_post.j2` | RSS post assessment template |
| `runtime/knowledge_pipeline.py` | Streaming Knowledge Pipeline (Phase 3c, optional) |
| `runtime/audit.py` | KafkaActivityEmitter — publishes execution trace to audit topic |
| `runtime/config/kafka.yaml` | Kafka topic and consumer config |

---

## Phase comparison

| | Phase 1 | Phase 2 | Phase 3 |
|---|---------|---------|---------|
| **Fetch trigger** | Manual click | Cron (30 min) | Cron (15 min) |
| **Queue** | asyncio.Queue | Postgres table | Kafka topic |
| **Event transport** | HTTP POST | HTTP POST | Kafka produce |
| **Worker coordination** | Single worker | SELECT FOR UPDATE SKIP LOCKED | Consumer group (automatic) |
| **Fault recovery** | Tasks lost on crash | DB row stays pending | Offset uncommitted → replays |
| **Event replay** | No | Manual re-enqueue | Native — seek offset + restart |
| **Prompt management** | Hardcoded string | Hardcoded string | Jinja2 template — edit without redeploy |
| **Knowledge freshness** | Static | Static | Continuously updated (Phase 3c) |
| **Audit trail** | None | None | Immutable Kafka log (Phase 3e) |
| **Dead-letter** | None | `status=dead` rows | `cuga.tasks.dlq` topic — replayable |
| **Enterprise fit** | Standalone | Standalone | Plugs into Kafka event mesh |
| **New infra** | None | Postgres | Kafka |
