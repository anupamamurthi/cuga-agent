# cuga-runtime Architecture — Phase 3

> **What changes:** The Postgres queue is replaced by Kafka. But Phase 3 is more than a queue swap — it is the moment the runtime becomes a **CUGA Streaming Runtime**: a full event-driven adapter that connects CUGA to the enterprise event mesh. CUGA core does not change. It still receives normal REST calls. The runtime handles everything around it.

---

## The strategic shift

Phase 1 and 2 give CUGA an async queue so callers don't block. Phase 3 gives CUGA **autonomous operation** — it wakes up when something happens in the world, not when a human asks a question.

> *"Instead of waiting for direct instructions, agents are designed to emit and listen for events autonomously."*
> — Confluent, "A Guide to Event-Driven Design for Agents"

```
Phase 1 / 2                          Phase 3
────────────────────────────         ──────────────────────────────────────

Human sends a task                   Business event fires automatically
Caller polls for result              Results fan out to downstream systems
CUGA answers a question              CUGA acts as an autonomous operator
Queue is internal plumbing           Runtime plugs into the enterprise event mesh
```

The CUGA Streaming Runtime is a **separate deployable component** — not baked into CUGA core. It calls CUGA via its public REST API. CUGA has no awareness that Kafka is involved.

```
┌─────────────────────────────────────────────────────────────┐
│                   CUGA Streaming Runtime                     │
│                  (this is what Phase 3 builds)               │
│                                                             │
│   Kafka Worker    Event Translator    Streaming Knowledge   │
│   (event trigger)  (event → prompt)   (live embeddings)     │
│                                                             │
│   Supervisor Transport    Audit Interceptor                 │
│   (Kafka A2A)             (governance log)                  │
└──────────┬───────────────────┬────────────────────┬─────────┘
           │ REST / SDK        │ vector store write  │ API boundary
           ▼                   ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│                      CUGA Core (unchanged)                   │
│  LangGraph · Policy System · Tools · HITL · CugaSupervisor  │
└─────────────────────────────────────────────────────────────┘
```

---

## The four pillars

Phase 3 is organized around four capabilities, each built in the runtime layer with zero CUGA core changes (except one opt-in hook in Pillar 4):

| Pillar | What it does | Infrastructure |
|--------|-------------|----------------|
| **Stream** | Kafka Worker — triggers CUGA from business events, scales horizontally | Kafka topics + consumer groups |
| **Connect** | Event Translator — converts structured events to natural language; domain-specific personas | Jinja2 templates + `personas.yaml` |
| **Process** | Streaming Knowledge Pipeline — keeps CUGA's vector store continuously fresh | Kafka Connect + Flink (or Python worker) |
| **Govern** | Immutable audit trail of every invocation, tool call, and policy decision | `ActivityEmitter` Protocol hook + Kafka audit topic |

---

## Pillar 1 — Stream: Kafka Worker

### What changes vs Phase 2

```
                Phase 2                          Phase 3
                ────────────────────────         ──────────────────────────────────

Queue           Postgres cuga_tasks table        Kafka topic: cuga.tasks.*
Worker          Polls with SELECT FOR UPDATE      consumer.poll() — push, no lag
                SKIP LOCKED every 500ms
Fault recovery  DB row stays pending if          Offset uncommitted → replays
                worker crashes                   automatically on restart
Event replay    Manual re-enqueue               Native — seek any offset
Dead-letter     status=dead rows                cuga.tasks.dlq topic — replayable
Multi-worker    DB-coordinated                  Kafka consumer group — automatic
Throughput      ~100s tasks/min                 ~10,000s tasks/min per partition
```

### Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  Enterprise event sources                                                 ║
║  RSS feeds · CRM webhooks · IT alerts · Scheduled jobs · …              ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  produce CugaTaskEvent
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Kafka  (Apache Kafka 3.x or Confluent Cloud)                            ║
║                                                                          ║
║   cuga.tasks.incoming  ──────────────────────────────────────────────   ║
║   cuga.tasks.crm       ──── domain-specific topics (Pillar 2)           ║
║   cuga.tasks.ops       ──────────────────────────────────────────────   ║
║   cuga.tasks.results   ◄─── worker publishes results here               ║
║   cuga.tasks.dlq       ◄─── failed tasks after max retries              ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  consumer group: cuga-runtime-workers
                ┌──────────┼──────────┬──────────┐
                ▼          ▼          ▼          ▼
          ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
          │ Worker 1 │ │ Worker 2 │ │ Worker 3 │ │ Worker N │
          │ poll()   │ │ poll()   │ │ poll()   │ │ poll()   │
          │ → CUGA   │ │ → CUGA   │ │ → CUGA   │ │ → CUGA   │
          │ commit   │ │ commit   │ │ commit   │ │ commit   │
          └──────────┘ └──────────┘ └──────────┘ └──────────┘
                           │  HTTP POST /stream
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)  — no awareness of Kafka                              ║
╚══════════════════════════════════════════════════════════════════════════╝
```

### Offset commit strategy — the core correctness guarantee

```python
# runtime/kafka_worker.py

class CugaKafkaWorker:
    async def run(self, topic: str):
        self.consumer.subscribe([topic])
        while True:
            msg = self.consumer.poll(timeout=5.0)
            if msg is None:
                continue

            event = CugaTaskEvent.parse_raw(msg.value())
            try:
                output = await self.cuga_client.invoke(
                    query=event.query,
                    thread_id=event.thread_id or event.task_id,
                )
                await self._publish_result(event, output)
                self.consumer.commit(msg)        # ← only after CUGA success

            except CugaClientError as e:
                attempt = await self._increment_retry(event.task_id)
                if attempt >= self.max_retries:
                    await self._publish_dlq(event, str(e))
                    self.consumer.commit(msg)    # ← DLQ, done
                # else: leave offset uncommitted — another worker reclaims
```

If a worker crashes between `poll()` and `commit()`, the session timeout fires, the partition is reassigned to a healthy worker, and the message replays. Tasks are never silently dropped.

### Kafka topic config

```yaml
# runtime/config/kafka.yaml

kafka:
  bootstrap_servers: "localhost:9092"
  consumer_group: "cuga-runtime-workers"
  topics:
    intake:  "cuga.tasks.incoming"
    results: "cuga.tasks.results"
    dlq:     "cuga.tasks.dlq"
  consumer:
    auto_offset_reset: "earliest"
    enable_auto_commit: false            # manual commit only
    max_poll_interval_ms: 300000         # CUGA tasks can take up to 5 min
  producer:
    acks: "all"
    enable_idempotence: true
  topic_config:
    intake:
      partitions: 12
      replication_factor: 3
      retention_ms: 604800000            # 7 days — enables replay
    dlq:
      retention_ms: -1                   # indefinite — never discard DLQ
```

### Event replay

```bash
# Re-run all events from the last 7 days through a new CUGA config
kafka-consumer-groups.sh \
  --group cuga-runtime-workers \
  --topic cuga.tasks.incoming \
  --reset-offsets --to-datetime 2026-04-07T00:00:00.000 \
  --execute

# Restart workers — they re-process from that offset forward
# No re-fetching from source. No manual re-queuing. Just seek and run.
```

---

## Pillar 2 — Connect: Event Translator + Domain Personas

This is what shifts CUGA from an AI assistant to an **autonomous enterprise operator**. Enterprise systems publish structured JSON events. The Event Translator converts them to natural language before CUGA ever sees them. CUGA receives a normal prompt — it has no awareness of where the trigger came from.

### Domain topic mapping

Each business domain gets a dedicated intake topic and a corresponding CUGA persona:

```yaml
# runtime/config/personas.yaml

personas:
  crm:
    topic: cuga.tasks.crm
    cuga_persona: crm_agent
    hitl_threshold: enterprise          # high-value leads require approval
  ops:
    topic: cuga.tasks.ops
    cuga_persona: ops_agent
    hitl_threshold: destructive         # restart/rollback requires approval
  compliance:
    topic: cuga.tasks.compliance
    cuga_persona: compliance_agent
    hitl_threshold: always
  ai_feed:
    topic: cuga.tasks.ai_feed
    cuga_persona: default
```

```bash
# Deploy persona-specific worker fleets
cuga-runtime kafka-worker --persona crm --config runtime/config/kafka.yaml
cuga-runtime kafka-worker --persona ops --config runtime/config/kafka.yaml
```

### Event Translator — structured event → natural language prompt

Raw business events are structured JSON. The translator uses Jinja2 templates to convert them into the natural language task message CUGA expects. One template file per event type.

```python
# runtime/event_translator.py

class EventTranslator:
    """
    Converts raw business events into CugaTaskEvents.
    Template selection: {source_system}/{event_type}.j2
    CUGA receives a normal prompt — it never sees the raw JSON.
    """
    def translate(self, raw_event: dict, persona: str) -> CugaTaskEvent:
        template_key = f"{raw_event['source']}/{raw_event['type']}"
        template = self._load_template(template_key)
        message = template.render(**raw_event['data'])
        return CugaTaskEvent(query=message, persona=persona, ...)
```

Template examples:

```jinja2
{# templates/crm/lead_created.j2 #}
Qualify this new lead and recommend the next action.

Lead: {{ lead.name }} at {{ lead.company }}
Source: {{ lead.source }}  ·  Interest: {{ lead.interest_area }}
Company size: {{ lead.company_size }}  ·  Deal estimate: {{ lead.deal_size }}

Score the lead 1–10, explain the key signals, and recommend:
active outreach, nurture sequence, or disqualify.


{# templates/ops/alert_fired.j2 #}
Investigate this alert and recommend remediation steps.

Alert: {{ alert.name }} (severity: {{ alert.severity }})
Service: {{ alert.service }}  ·  Error: {{ alert.message }}
Duration: {{ alert.duration }}  ·  Fired at: {{ alert.fired_at }}

Check relevant runbooks, review recent deployments, propose a root cause
hypothesis and immediate next steps.


{# templates/ai_feed/rss_post.j2 #}
{{ query_prompt }}

Context:
  feed_name: {{ feed_name }}
  title: {{ title }}
  text: {{ text }}
  url: {{ url }}
  posted_at: {{ posted_at }}
```

### HITL pass-through via Kafka

When CUGA raises a HITL interrupt, the runtime externalises the wait as Kafka events — no blocking thread held open:

```
Worker detects HITL interrupt
        │
        ▼
Produce to cuga.hitl.pending:
  { task_id, thread_id, question, options, context }
        │
        ▼
Human review interface (Slack, approval UI, email)
  consumes event, presents to reviewer
        │
        ▼  reviewer approves / rejects / edits
Produce to cuga.hitl.responses:
  { task_id, approved: true, override: null }
        │
        ▼
Worker consumes response, resumes CUGA:
  agent.invoke(action_response=ApproveAction(...), thread_id=...)
```

No CUGA HITL logic changes. The runtime handles the async wait externally.

---

## Pillar 3 — Process: Streaming Knowledge Pipeline

CUGA's knowledge base is currently static. Pillar 3 makes it continuously current — the same `knowledge_search` tool CUGA already calls retrieves content that was updated minutes ago, not weeks ago.

```
Enterprise sources → Kafka Connect connectors → document events (Kafka)
                                                        │
                                                        ▼
                                          StreamingKnowledgePipeline (runtime)
                                          - chunk text
                                          - generate embeddings
                                          - upsert/delete in vector store
                                                        │
                                                        ▼
                                          vector store  (shared with CUGA Core)
                                                        │
                                          CUGA's knowledge_search tool
                                          retrieves fresh context automatically
```

```python
# runtime/knowledge_pipeline.py

class StreamingKnowledgePipeline:
    """
    Consumes DocumentEvents from Kafka.
    Writes to the same vector store CUGA already reads.
    CUGA core is untouched — it benefits automatically.
    """
    async def run(self):
        self.consumer.subscribe(["enterprise.docs.*"])
        async for msg in self._iter_messages():
            event = DocumentEvent.parse_raw(msg.value())
            if event.operation == "upsert":
                chunks = self._chunk(event.content)
                embeddings = await self._embed(chunks)
                await self.vector_store.upsert(event.document_id, embeddings, event.metadata)
            elif event.operation == "delete":
                await self.vector_store.delete(event.document_id)
            self.consumer.commit(msg)
```

### Flink vs Python worker

| | Python Worker | Flink AI Model Inference |
|---|---|---|
| When to use | < a few hundred docs/hour | Production scale, many sources |
| Ops cost | Simple — same process as runtime | Requires Flink cluster |
| Interface | Same vector store write | Same vector store write |

The vector store interface is identical — swap is operational, not architectural.

### Kafka Connect for source integration

Rather than writing custom producers per enterprise system, use Confluent's pre-built connectors. Each feeds `DocumentEvent`s to the pipeline with no custom code:

```yaml
# Confluence — runbooks, specs, wikis
connector: ConfluentConnector
config:
  topics: enterprise.docs.confluence
  poll_interval: 60s

# Salesforce — account history, opportunity notes
connector: SalesforceConnector
config:
  topics: enterprise.docs.salesforce
  objects: [Account, Opportunity, Case]

# Slack — engineering channel discussions, incident retrospectives
connector: SlackConnector
config:
  topics: enterprise.docs.slack
  channels: [engineering, support, incidents]
```

### Live state via ksqlDB + MCP

Document knowledge (RAG) answers "what do the runbooks say?" Live state answers "what is the current error rate, right now?" These are two different tools — ksqlDB materializes Kafka streams into queryable tables; CUGA calls them as MCP tools.

```sql
-- ksqlDB continuously updates this table from the raw Kafka stream
CREATE TABLE SERVICE_HEALTH AS
    SELECT service, AVG(error_rate) AS error_rate, MAX(latency_p99) AS latency_p99
    FROM METRICS_STREAM
    WINDOW TUMBLING (SIZE 1 MINUTE)
    GROUP BY service
    EMIT CHANGES;
```

```python
# runtime/mcp_tools.py — registered with CUGA at startup, not in CUGA core

@mcp_tool
def get_service_health(service: str) -> dict:
    """Query current error rate and latency for a service."""
    return ksqldb_client.query(
        f"SELECT * FROM SERVICE_HEALTH WHERE service='{service}';"
    )
```

CUGA calls `get_service_health("auth-api")` exactly like any other tool. The streaming infrastructure is invisible to the agent.

> **Key principle:** Agents query state, not streams. ksqlDB materializes streams into tables. CUGA calls a tool. No Kafka knowledge leaks into the agent.

---

## Pillar 4 — Govern: Immutable Audit Trail

Because all CUGA task traffic flows through the runtime's Kafka topics, every invocation is already in the log. For full fidelity — tool calls, policy decisions, intermediate reasoning steps — the runtime registers an `ActivityEmitter` with CUGA at startup.

**This is the one small addition to CUGA core** — a Protocol with a single method and zero external dependencies:

```python
# In CUGA core — no Kafka, no external deps, no behavior change
class ActivityEmitter(Protocol):
    def emit(self, step: ActivityStep) -> None: ...

class ActivityTracker:
    def __init__(self, ..., emitter: ActivityEmitter | None = None):
        self.emitter = emitter  # None by default — behavior unchanged

    def record_step(self, step: ActivityStep):
        # existing behavior: write to markdown — unchanged
        if self.emitter:
            self.emitter.emit(step)     # runtime receives this if registered
```

```python
# In CUGA Streaming Runtime — all Kafka specifics live here, never in core
class KafkaActivityEmitter:
    def emit(self, step: ActivityStep):
        self.producer.produce(
            "cuga.audit.executions",
            key=step.thread_id,
            value=AuditEvent(
                thread_id=step.thread_id,
                task_id=step.task_id,
                step_type=step.type,        # tool_call | llm_call | policy_check | hitl | answer
                agent_name=step.agent,
                persona=step.persona,
                input=step.input,
                output=step.output,
                policy_applied=step.policy,
                duration_ms=step.duration_ms,
            ).json()
        )
```

Without a registered emitter, CUGA behaves exactly as it does today.

The audit log enables:
- Full reconstructible record of every agent action for any task
- Regulatory compliance (GDPR, SOC2, HIPAA, EU AI Act) — indefinite retention, field-level encryption
- Replay any past CUGA execution for debugging or compliance review

---

## CugaSupervisor on Kafka (multi-agent coordination)

CugaSupervisor already orchestrates multi-agent workflows via HTTP/SSE. Phase 3 adds a Kafka transport option — same orchestration logic, elastic fault tolerance, no bespoke failure handling.

```
CugaSupervisor (uses KafkaA2ATransport)
    │
    │ publishes to: cuga.supervisor.tasks (partitioned by agent_name)
    ▼
[Worker CUGA #1]  [Worker CUGA #2]  [Worker CUGA #3]   ← consumer group
    │
    │ each publishes to: cuga.supervisor.results
    ▼
CugaSupervisor aggregates
```

```python
# runtime/supervisor_transport.py

class KafkaA2ATransport:
    """Implements CugaSupervisor's existing A2ATransportInterface using Kafka."""
    async def delegate(self, agent_name: str, task: AgentTask) -> str:
        self.producer.produce(
            "cuga.supervisor.tasks",
            key=agent_name,             # partition key = agent name
            value=task.json()
        )
        return task.task_id

    async def await_result(self, task_id: str, timeout: float) -> AgentResult:
        # poll cuga.supervisor.results for matching task_id
        ...
```

A **Blackboard topic** (`cuga.supervisor.blackboard`) lets worker agents share intermediate findings during complex workflows — filling a gap in current CugaSupervisor where sub-agents can't share context without explicit wiring.

Partition key = `agent_name` ensures all tasks for a given specialist hit the same partition, preserving thread continuity. The Kafka Consumer Rebalance Protocol handles worker death and load redistribution — the supervisor needs no bespoke failure logic.

---

## Flink's role — what it does and what it doesn't

Flink appears in Phase 3 but only in two specific roles. Getting this boundary right matters.

| Role | What Flink does | Applies? |
|------|----------------|---------|
| **Data enrichment** | Joins, filters, aggregations before events reach CUGA intake topics | Yes — upstream of `cuga.tasks.*` |
| **Embedding pipeline** | Generates embeddings from streaming docs at scale | Yes — in Streaming Knowledge |
| **Agent orchestration** | Routes messages between agents, sequences calls | **No** — CugaSupervisor owns this |

CugaSupervisor remains the agent orchestrator. Flink prepares data for CUGA. They operate at different layers and do not overlap.

```
raw enterprise events
        │
        ▼
  Flink  (enrichment)
  - join lead with account history
  - correlate alert with recent deployments
  - filter and deduplicate RSS posts
        │
        ▼
  cuga.tasks.*  (enriched events)
        │
        ▼
  CugaKafkaWorker → CUGA → CugaSupervisor → worker agents
```

---

## What each layer owns

```
┌───────────────────────────────────────────────────────────────────┐
│  CONFLUENT PLATFORM                                               │
│  Kafka topics · ksqlDB · Kafka Connect · Schema Registry          │
│  Owned by: infrastructure / data engineering team                 │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  CUGA STREAMING RUNTIME  (Phase 3 — what we build)                │
│                                                                   │
│  • CugaKafkaWorker — consumes task events, calls CUGA REST API    │
│  • EventTranslator — structured event → natural language prompt   │
│  • StreamingKnowledgePipeline — Flink/Python embeddings           │
│  • ksqlDB MCP Tools — expose live state as agent tools            │
│  • KafkaA2ATransport — elastic CugaSupervisor coordination        │
│  • KafkaActivityEmitter — publishes execution trace               │
│                                                                   │
│  Owned by: AI platform / agent infrastructure team               │
└──────────────────────────────┬────────────────────────────────────┘
                               │ REST / MCP / vector store
┌──────────────────────────────▼────────────────────────────────────┐
│  CUGA CORE  (unchanged)                                            │
│                                                                   │
│  • CugaSupervisor — orchestration, routing, task delegation       │
│  • CugaLiteSubgraph — reasoning loop, planning, code-act          │
│  • Policy System — Intent Guard, Tool Approval, Playbook          │
│  • Tool Providers — MCP, OpenAPI, LangChain tools                 │
│  • knowledge_search — semantic retrieval from vector store        │
│  • HITL — human-in-the-loop interrupts                           │
│  • ActivityTracker — execution trace (+ ActivityEmitter hook)     │
│                                                                   │
│  Owned by: AI / agent product team                               │
└───────────────────────────────────────────────────────────────────┘
```

---

## Phase 2 → Phase 3 migration

```
1. Deploy Kafka (Apache Kafka 3.x locally, or Confluent Cloud)
2. Create topics (kafka-topics.sh or Confluent Cloud UI)
3. Replace TaskWorker with CugaKafkaWorker
4. Update adapter services: POST /events → produce to Kafka topic
   (HTTP API in app.py can remain for backward compat — dual-path is fine)
5. Deploy EventTranslator templates for each event source
6. Deploy N workers as the consumer group
7. Optionally: add StreamingKnowledgePipeline for live vector store updates
8. Optionally: register KafkaActivityEmitter for full audit fidelity
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CUGA_KAFKA_BOOTSTRAP` | — | Kafka bootstrap servers |
| `CUGA_KAFKA_GROUP` | `cuga-runtime-workers` | Consumer group ID |
| `CUGA_KAFKA_TOPIC_INTAKE` | `cuga.tasks.incoming` | Intake topic |
| `CUGA_KAFKA_TOPIC_RESULTS` | `cuga.tasks.results` | Results topic |
| `CUGA_KAFKA_TOPIC_DLQ` | `cuga.tasks.dlq` | Dead-letter topic |
| `CUGA_KAFKA_CONFIG` | — | Path to `kafka.yaml` |
| `CUGA_API_URL` | `http://localhost:7860` | CUGA server URL |
| `CUGA_TIMEOUT` | `300` | Seconds to wait for CUGA |
| `CUGA_MAX_RETRIES` | `3` | Attempts before DLQ |

---

## Build sequence

| Phase | What it builds | CUGA core change |
|-------|---------------|-----------------|
| **3a — Kafka Worker** | Consumer/producer wrapper, offset commit, DLQ | None |
| **3b — Event Translator + Personas** | Domain topics, Jinja2 templates, persona-aware workers | None |
| **3c — Streaming Knowledge** | Embedding pipeline, Kafka Connect, ksqlDB MCP tools | None |
| **3d — Supervisor Transport** | KafkaA2ATransport, Blackboard topic | None (uses existing interface) |
| **3e — Audit + Governance** | KafkaActivityEmitter, audit topic, replay | `ActivityEmitter` Protocol hook (opt-in) |

Recommended order: 3a → 3b → 3c and 3d in parallel → 3e in parallel after 3a.
