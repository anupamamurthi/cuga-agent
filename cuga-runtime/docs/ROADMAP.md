# cuga-runtime Roadmap

> From manual tool to autonomous streaming operator — six incremental phases, each independently shippable.

Each phase is a working system. You can stop at any phase and have something valuable. Later phases add capability, not correctness.

---

## Overview

```
Phase 1   asyncio.Queue + manual UI                    → working prototype
Phase 2   Postgres queue + scheduled fetching          → production-ready
Phase 3a  Kafka transport (Stream)                     → fault-tolerant, replayable
Phase 3b  EventTranslator (Connect)                    → prompt management decoupled
Phase 3c  StreamingKnowledgePipeline (Process)         → context-aware assessments
Phase 3d  ActivityEmitter audit trail (Govern)         → verifiable, improvable
```

---

## Phase 1 — Working Prototype

**The system:** Manual UI. Click "Fetch Feeds." Click "Analyze." Results live in memory, gone on refresh.

**What it proves:** The CUGA assessment loop works end-to-end. RSS → prompt → structured answer.

**What it can't do:**
- No persistence — reload the page, results gone
- No automation — someone must click
- One worker, sequential processing
- No retry on failure

**Infrastructure:** None beyond running CUGA.

**Exit criteria:** CUGA correctly classifies at least one real RSS post as relevant/irrelevant with the right format.

---

## Phase 2 — Production-Ready Automation

**The change:** Replace `asyncio.Queue` with a Postgres-backed task table. Add APScheduler for automatic feed polling. UI becomes a read-only persistent dashboard.

**What's new:**
- Tasks survive process restarts and crashes
- Feeds polled automatically every 30 minutes — no human needed
- N workers compete for tasks via `SELECT FOR UPDATE SKIP LOCKED`
- Exponential backoff retry (up to 4 attempts)
- `X-API-Key` authentication on `POST /events`
- `seen_posts` table deduplicates across restarts

**What doesn't change:** `worker.py`, `client.py`, `app.py`, `schemas.py`, CUGA core. The event shape is identical.

**Infrastructure added:** Postgres (one table, one index).

**Exit criteria:**
- Feed watcher runs for 24 hours unattended, processes all new posts, no duplicates
- Worker process can be restarted mid-task without losing the task
- Results visible in the dashboard after page reload

---

## Phase 3a — Stream: Kafka Replaces Postgres Queue

**The change:** `TaskWorker` becomes `CugaKafkaWorker`. Feed watcher produces to `cuga.tasks.ai_feed` instead of calling `POST /events`. Kafka consumer group replaces `SELECT FOR UPDATE SKIP LOCKED`.

**What's new:**
- Offset committed **only after CUGA returns success** — crash mid-task = event replays, not lost
- Consumer group auto-rebalances when workers are added or removed
- Topic key = `post.source` → consistent partition per feed → ordering preserved within each feed
- 7-day topic retention → replay any historical window without re-fetching RSS

**What doesn't change:** Event schema (`CugaTaskEvent`), CUGA core, all downstream result handling. Only the transport changes.

**Infrastructure added:** Kafka (one topic `cuga.tasks.ai_feed`, one topic `cuga.tasks.results`, one DLQ `cuga.tasks.dlq`).

**The replay capability (new):** Reset consumer group offset to a past timestamp, restart workers — system re-scores that entire window through the current prompt. RSS feeds don't need to be re-fetched.

```bash
kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --group cuga-runtime-workers \
  --topic cuga.tasks.ai_feed \
  --reset-offsets --to-datetime 2026-04-07T00:00:00.000 \
  --execute
```

**Exit criteria:**
- Kill a worker mid-assessment → task replays on restart, no duplicate result
- Add a second worker → both process tasks, no double-processing
- Reset offset to 3 days ago → system re-scores that window without re-fetching RSS

---

## Phase 3b — Connect: EventTranslator Decouples Prompts

**The change:** Add `EventTranslator` — a Jinja2 template layer that converts structured JSON events into CUGA prompts. The prompt moves out of Python source code and into a template file.

**What's new:**
- `runtime/event_translator.py` — renders template from structured event dict
- `runtime/templates/ai_feed/rss_post.j2` — the actual prompt, editable without redeploy
- Domain topic routing — different event types can map to different topics and templates
- HITL support — ambiguous assessments routed to `cuga.tasks.hitl` for human review

**What doesn't change:** Kafka topics, worker logic, CUGA core. EventTranslator is called once, before the worker sends the prompt to CUGA.

**Before (Phase 3a):** Prompt is a Python string constant. Change it = redeploy the feed watcher.

**After (Phase 3b):** Prompt is a Jinja2 template. Change it = edit a file. Re-score historical events through the new template via offset reset. No service redeploy.

```
rss_post.j2 change → offset reset → workers re-run old events → new scores
                                     (feed watcher unchanged, CUGA unchanged)
```

**Exit criteria:**
- Edit `rss_post.j2` to add a new output field (e.g., `category: research|product|opinion`)
- Replay 3 days of historical events — all results now include the new field
- Zero code changes outside the template file

---

## Phase 3c — Process: Streaming Knowledge Pipeline

**The change:** Add `StreamingKnowledgePipeline` — a Kafka consumer that ingests past blog posts, chunks content, generates embeddings, and upserts into a vector store. CUGA's `knowledge_search` tool queries this store at assessment time.

**What's new:**
- `runtime/knowledge_pipeline.py` — subscribes to `enterprise.docs.ai_feed`
- Feed watcher publishes full post content to `enterprise.docs.ai_feed` in addition to the task event
- Embeddings pipeline: chunk → embed → upsert to vector store (e.g., MongoDB Atlas, Qdrant)
- CUGA gains access to prior coverage of any topic

**What doesn't change:** Everything from 3a and 3b. Knowledge pipeline is additive — CUGA assessments continue working without it (they just lack prior context).

**What this enables:**

| Without 3c | With 3c |
|---|---|
| "Is parallel tool use relevant to agentic AI?" → CUGA answers from the post alone | CUGA retrieves 3 prior posts on the topic → answers in context of prior coverage |
| Every assessment is stateless | Assessments are context-aware — signal is calibrated against what's already known |
| "High signal" means the post itself is interesting | "High signal" means it's interesting **and new** |

**Flink alternative:** For production scale, Flink AI Model Inference replaces the Python embedding pipeline. Same Kafka topics, same vector store, higher throughput and lower latency.

**Exit criteria:**
- Let the pipeline run for 1 week, indexing all assessed posts
- Submit a post that covers a topic seen before → CUGA's summary references prior coverage
- Submit a post on a genuinely new topic → CUGA's summary does not falsely claim prior coverage

---

## Phase 3d — Govern: Audit Trail for Every Assessment

**The change:** Register `KafkaActivityEmitter` at CUGA startup. Every tool call, LLM inference, and policy decision is published to `cuga.audit.executions` as an immutable Kafka record.

**What's new:**
- `runtime/audit.py` — `KafkaActivityEmitter` implementing the `ActivityEmitter` protocol
- `cuga.audit.executions` topic — one record per CUGA activity step
- Full execution trace: input, output, duration, policy applied, timestamp
- CUGA core unchanged — `ActivityEmitter` is a one-method protocol hook, opt-in

**What doesn't change:** Everything from 3a, 3b, 3c. Audit is purely additive — removing the emitter registration returns to pre-3d behavior.

**What each audit record contains:**

```json
{
  "thread_id": "...",
  "task_id": "...",
  "step_type": "llm_call",
  "input": "You are monitoring AI blogs... feed_name: Anthropic Blog...",
  "output": "relevant: yes\nsignal: high\nsummary: ...",
  "duration_ms": 4820,
  "policy_applied": null,
  "timestamp": "2026-04-14T10:23:45Z"
}
```

**What this enables:**

| Question | How to answer |
|---|---|
| Why did CUGA mark this post irrelevant on April 7? | Seek to that offset in `cuga.audit.executions`, read the exact input and output |
| Did changing the template in 3b improve signal quality? | Compare assessment distributions before and after the template change using the audit log |
| What was the prompt sent to the model for this specific post? | Read the `llm_call` record for that `task_id` |
| Compliance: what is the evidence chain for this auto-drafted Slack post? | Retrieve the full trace from `thread_id` |

**Exit criteria:**
- Every assessment produces records in `cuga.audit.executions`
- Given a `task_id`, reconstruct the full input/output trace from the audit log alone
- Compare signal distributions for 1 week before and after a template change using only audit log data

---

## Dependency Map

```
Phase 1
  └── Phase 2  (add Postgres + scheduler)
        └── Phase 3a  (swap queue transport to Kafka)
              └── Phase 3b  (add EventTranslator — no Kafka changes)
                    └── Phase 3c  (add knowledge pipeline — no worker changes)
                          └── Phase 3d  (add audit emitter — no pipeline changes)
```

Each phase builds on the previous. None require changes to phases already complete. CUGA core is untouched across all phases.

---

## What Changes at Each Phase

| | Phase 1 | Phase 2 | Phase 3a | Phase 3b | Phase 3c | Phase 3d |
|---|---|---|---|---|---|---|
| **Queue** | asyncio.Queue | Postgres | Kafka | Kafka | Kafka | Kafka |
| **Trigger** | Manual click | Cron (30 min) | Cron (15 min) | Cron (15 min) | Cron (15 min) | Cron (15 min) |
| **Workers** | 1 | N (SKIP LOCKED) | Consumer group | Consumer group | Consumer group | Consumer group |
| **Fault recovery** | Lost on crash | Row stays queued | Offset replays | Offset replays | Offset replays | Offset replays |
| **Event replay** | No | Manual re-enqueue | Native (offset reset) | Native + new template | Native + new template | Native + audit diff |
| **Prompt management** | Hardcoded | Hardcoded | Hardcoded | Jinja2 template | Jinja2 template | Jinja2 template |
| **Agent memory** | None | None | None | None | Vector store (continuous) | Vector store (continuous) |
| **Audit trail** | None | None | None | None | None | Immutable Kafka log |
| **New infra** | None | Postgres | Kafka | None | Vector store | None |
| **CUGA core changes** | — | None | None | None | None | None |

---

## Recommended Sequence

**Ship Phase 2 first.** It solves real problems (persistence, automation, retry) with minimal new infrastructure and zero changes to the CUGA core. It's the highest return on effort.

**Ship Phase 3a next.** The Kafka transport upgrade is the foundation everything else depends on. Once the offset model is in place, 3b, 3c, and 3d are all additive.

**3b before 3c.** You want the EventTranslator in place before you start investing in prompt tuning — otherwise you can't replay historical events through improved prompts.

**3d any time after 3a.** The audit emitter is independent of 3b and 3c. If compliance or prompt measurement is the priority, ship 3d right after 3a.

**3c last.** Streaming knowledge is the highest-effort addition and requires a vector store. Ship it when you have enough historical data to make it useful.
