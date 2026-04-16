# CUGA Streaming Runtime: Implementation Roadmap

*The event-driven capability stack for CUGA — built entirely outside CUGA core.*

---

## Foundational Principle

The **CUGA Streaming Runtime** is a separate deployable component. It wraps CUGA via its public REST API and Python SDK. CUGA core is not modified — it has no awareness that Kafka is involved.

```
┌──────────────────────────────────────────────────────────────┐
│                   CUGA Streaming Runtime                      │
│                  (this is what we build)                      │
│                                                               │
│   Kafka Worker    Streaming Knowledge    Audit Interceptor    │
│   event triggers   live embeddings       governance log       │
└──────────┬───────────────────┬────────────────────┬──────────┘
           │ REST / SDK        │ vector store write  │ API boundary
           ↓                   ↓                     ↓
┌──────────────────────────────────────────────────────────────┐
│                      CUGA Core (unchanged)                    │
│   LangGraph · Policy System · Tools · HITL · CugaSupervisor  │
└──────────────────────────────────────────────────────────────┘
```

**Infrastructure:**
- Kafka: Apache Kafka 3.x or Confluent Cloud
- Python client: `confluent-kafka` (Confluent's client — better async support than `kafka-python`)
- Agent framework: LangGraph (already in CUGA — Confluent explicitly names it as the integration target)
- Stream processing: Apache Flink (for embedding pipeline in Phase 3; optional Python worker for simpler deployments)

**The one small addition to CUGA core** (Phase 5, optional): An `ActivityEmitter` Protocol — one method, zero external dependencies — that the runtime registers to receive mid-execution events for full audit fidelity. If not registered, CUGA behaves exactly as today.

---

## Phase 1 — Kafka Worker
**What:** The runtime listens on Kafka topics and calls CUGA's REST API for each task event.
**Why first:** Everything else builds on this. Also the fastest path to a demo.
**CUGA core changes:** None.

### What to build

**1a. Task and Result Schemas**

The canonical event types that flow between the runtime and enterprise systems:

```python
# runtime/schemas.py

class CugaTaskEvent(BaseModel):
    task_id: str           # UUID — for correlation and idempotency
    thread_id: str | None  # maps to CUGA thread_id; enables conversation resumption
    message: str           # natural language task (assembled by EventTranslator)
    persona: str | None    # which CUGA persona config to load
    priority: int = 5      # used in partition key strategy
    metadata: dict = {}    # arbitrary caller context (source system, user_id, etc.)
    timestamp: datetime

class CugaResultEvent(BaseModel):
    task_id: str           # correlates back to CugaTaskEvent
    thread_id: str
    answer: str
    tool_calls: list
    error: str | None
    duration_ms: int
    timestamp: datetime
```

**1b. Kafka Worker**

The core runtime component. Pulls task events, calls CUGA, publishes results.

```python
# runtime/worker.py

class CugaKafkaWorker:
    """
    Subscribes to a Kafka intake topic.
    For each CugaTaskEvent: calls CUGA's REST API, publishes CugaResultEvent.
    Commits offset only after successful CUGA response.
    Failed tasks go to dead-letter topic for human review.
    """
    def __init__(self, cuga_base_url: str, kafka_config: KafkaConfig): ...
    async def run(self): ...
    async def _call_cuga(self, event: CugaTaskEvent) -> CugaResultEvent: ...
    async def _handle_failure(self, event: CugaTaskEvent, error: Exception): ...
```

Key decisions:
- **Offset commit strategy:** commit only after CUGA returns successfully. If the worker crashes mid-call, the offset is not committed — the task replays on restart with no data loss.
- **Idempotency:** check `task_id` against a short-lived cache (Redis or in-memory) before calling CUGA, to skip already-processed events on replay.
- **Dead-letter queue:** after N retries, publish to `cuga.tasks.dlq`. DLQ events can optionally surface to CUGA's existing HITL mechanism for human review.
- **Long-running tasks:** CUGA tasks can take 30-120s. Set `max_poll_interval_ms` accordingly — default Kafka timeout (5 min) is usually sufficient, but configure explicitly.

**1c. Kafka Configuration**

```yaml
# runtime/config/kafka.yaml

kafka:
  bootstrap_servers: "localhost:9092"   # or Confluent Cloud endpoint
  consumer_group: "cuga-runtime-workers"
  topics:
    intake: "cuga.tasks.incoming"
    results: "cuga.tasks.results"
    dlq: "cuga.tasks.dlq"
  consumer:
    max_poll_interval_ms: 300000
    auto_offset_reset: "earliest"
    enable_auto_commit: false          # manual commit after success only
  producer:
    acks: "all"
    enable_idempotence: true
```

**1d. CLI Entry Point**

```bash
# Start a runtime worker pointing at a CUGA instance
cuga-runtime worker --cuga-url http://localhost:8000 --config runtime/config/kafka.yaml
```

### Runtime files
```
runtime/
  schemas.py       task + result event models
  worker.py        Kafka consumer → CUGA REST → Kafka producer
  producer.py      result publishing helper
  config.py        KafkaConfig dataclass
  config/
    kafka.yaml
```

### Milestone
A task event published to `cuga.tasks.incoming` is consumed by the runtime worker, processed by CUGA, and the result appears on `cuga.tasks.results`. CUGA Core logs show a normal REST invocation — no Kafka awareness.

---

## Phase 2 — Event-Triggered Personas
**What:** Domain-specific Kafka topics, each mapped to a CUGA persona. Enterprise systems trigger CUGA by publishing events — no human prompt required.
**Prerequisite:** Phase 1.
**CUGA core changes:** None.

### What to build

**2a. Domain Topic Map**

Each business domain gets a dedicated intake topic and a corresponding CUGA persona config:

```yaml
# runtime/config/personas.yaml

personas:
  crm:
    topic: cuga.tasks.crm
    cuga_persona: crm_agent          # references a CUGA persona config
    hitl_threshold: enterprise       # high-value leads require approval
  ops:
    topic: cuga.tasks.ops
    cuga_persona: ops_agent
    hitl_threshold: destructive      # restart/rollback requires approval
  compliance:
    topic: cuga.tasks.compliance
    cuga_persona: compliance_agent
    hitl_threshold: always           # all compliance outputs require review
  finance:
    topic: cuga.tasks.finance
    cuga_persona: finance_agent
    hitl_threshold: high_value
```

**2b. Event Translator**

Raw business events are structured JSON, not natural language prompts. The translator converts them before calling CUGA:

```python
# runtime/event_translator.py

class EventTranslator:
    """
    Converts structured business events into CugaTaskEvents.
    Uses Jinja2 templates per event_type — one template file per source system.
    """
    def translate(self, raw_event: dict, persona: str) -> CugaTaskEvent: ...
```

Template examples:
```jinja2
{# templates/crm/lead_created.j2 #}
Qualify this new lead and determine the appropriate next action.

Lead: {{ lead.name }} at {{ lead.company }}
Source: {{ lead.source }}
Interest: {{ lead.interest_area }}
Company size: {{ lead.company_size }}

Enrich with available context, score the lead, and recommend: active outreach, nurture sequence, or disqualify.

{# templates/ops/alert_fired.j2 #}
Investigate this alert and recommend remediation steps.

Alert: {{ alert.name }} (severity: {{ alert.severity }})
Service: {{ alert.service }}
Error: {{ alert.message }}
Time: {{ alert.fired_at }}

Check relevant runbooks, review recent deployments, and propose a root cause hypothesis and next steps.
```

CUGA receives a normal natural language prompt. It has no awareness that this came from a webhook.

**2c. Persona-Aware Worker**

Extend the Phase 1 worker to accept persona config at startup:

```bash
# Deploy a fleet of specialized workers
cuga-runtime worker --persona crm        --config runtime/config/kafka.yaml
cuga-runtime worker --persona ops        --config runtime/config/kafka.yaml
cuga-runtime worker --persona compliance --config runtime/config/kafka.yaml
```

Multiple worker instances per persona form a consumer group automatically — Kafka handles load distribution.

**2d. HITL Pass-Through**

When CUGA raises a HITL interrupt (via its existing LangGraph interrupt mechanism), the runtime:
1. Publishes a `cuga.hitl.pending` event with the interrupt details
2. Waits for a `cuga.hitl.responses` event (from a human review interface)
3. Resumes CUGA via `agent.invoke(action_response=...)` with the approval or denial

No changes to CUGA's HITL logic — the runtime handles the async wait externally.

### Runtime files added
```
runtime/
  event_translator.py
  templates/
    crm/lead_created.j2
    ops/alert_fired.j2
    compliance/regulation_updated.j2
    ...
  config/
    personas.yaml
```

### Milestone
A Salesforce lead event publishes to `cuga.tasks.crm`. The runtime translates it, calls CUGA's CRM persona, and posts the qualification result back to Salesforce via the result topic — with no human involved. High-value leads pause at HITL and resume on approval.

---

## Phase 3 — Streaming Knowledge Base
**What:** A continuous embedding pipeline keeps CUGA's vector store current from live enterprise sources.
**Prerequisite:** Kafka infra from Phase 1.
**CUGA core changes:** None. CUGA's `knowledge_search` tool reads the same vector store — it automatically benefits.

### What to build

**3a. Document Event Schema**

```python
# runtime/schemas.py (extend)

class DocumentEvent(BaseModel):
    document_id: str        # stable ID — enables idempotent upsert and deletion
    source: str             # "confluence", "salesforce", "slack", "gdrive", etc.
    content: str            # raw text
    metadata: dict          # title, author, timestamp, access_level, tags
    operation: Literal["upsert", "delete"]
    timestamp: datetime
```

**3b. Streaming Knowledge Pipeline**

```python
# runtime/knowledge_pipeline.py

class StreamingKnowledgePipeline:
    """
    Consumes DocumentEvents from Kafka.
    Chunks text, generates embeddings, upserts/deletes from CUGA's vector store.
    CUGA's knowledge_search tool automatically retrieves fresh embeddings.
    """
    def __init__(
        self,
        embedding_model,      # same model CUGA already uses
        vector_store_client,  # same vector store CUGA reads from
        kafka_config: KafkaConfig
    ): ...

    async def run(self): ...
    async def _process(self, event: DocumentEvent): ...
    async def _chunk_and_embed(self, content: str) -> list[Embedding]: ...
```

For higher-throughput deployments, replace the Python worker with a Flink job using `Flink AI Model Inference` for embedding generation — the Confluent paper's recommended approach for production scale.

**3c. Kafka Connect for Source Integration**

Rather than writing custom producers per enterprise source, use Confluent's pre-built connectors:

```yaml
# Confluent Connect configuration examples

# Confluence connector — documents update automatically
connector: ConfluentConnector
config:
  topics: enterprise.docs.confluence
  poll_interval: 60s

# Salesforce connector — account and opportunity data
connector: SalesforceConnector
config:
  topics: enterprise.docs.salesforce
  objects: [Account, Opportunity, Case]

# Slack connector — operational knowledge from channels
connector: SlackConnector
config:
  topics: enterprise.docs.slack
  channels: [#engineering, #support, #product]
```

These feed `DocumentEvent`s to the pipeline with no custom code per source.

**3d. Vector Store Thread Safety**

The pipeline writes concurrently; CUGA reads concurrently. Confirm the vector store client (Pinecone, MongoDB Atlas, Elasticsearch, etc.) is safe for concurrent access — most managed vector stores are; local FAISS instances are not.

### Runtime files added
```
runtime/
  knowledge_pipeline.py
  connectors/
    config/confluence.yaml
    config/salesforce.yaml
    config/slack.yaml
```

### Milestone
An engineer updates a runbook in Confluence. Within minutes, the streaming pipeline has re-embedded it. A CUGA ops agent asked about the incident procedure retrieves the updated runbook — with no manual knowledge base refresh.

---

## Phase 4 — Multi-Agent Coordination via Kafka
**What:** CugaSupervisor uses Kafka topics for worker coordination instead of direct HTTP/SSE calls.
**Prerequisite:** Phase 1.
**CUGA core changes:** One transport option added to CugaSupervisor's existing A2A transport interface — the interface is already abstracted.

### What to build

**4a. Kafka A2A Transport (Runtime)**

CugaSupervisor already has an `A2ATransportInterface`. The runtime provides a Kafka implementation:

```python
# runtime/supervisor_transport.py

class KafkaA2ATransport:
    """
    Implements CugaSupervisor's A2ATransportInterface using Kafka.
    Supervisor publishes delegation events; workers pull as consumer group.
    Partition key = agent_name for stateful task continuity.
    """
    async def delegate(self, agent_name: str, task: AgentTask) -> str:
        # publish to cuga.supervisor.tasks, key=agent_name
        # return task_id for result correlation
        ...

    async def await_result(self, task_id: str, timeout: float) -> AgentResult:
        # poll cuga.supervisor.results for matching task_id
        ...
```

**4b. Topic Architecture**

```
cuga.supervisor.tasks     (partitioned by agent_name)
    ↓
Worker CUGA instances     (consumer group: cuga-supervisor-workers)
    ↓
cuga.supervisor.results   (keyed by task_id)
    ↓
CugaSupervisor aggregates
```

Partition key = `agent_name` ensures all tasks for a given specialized agent hit the same partition, preserving stateful context. The Kafka Consumer Rebalance Protocol handles worker scaling and failure redistribution — supervisor needs no bespoke failure logic.

**4c. Blackboard Topic**

A shared topic for cross-agent knowledge sharing during complex workflows:

```python
# runtime/blackboard.py

class KafkaBlackboard:
    """
    Shared Kafka topic that all agents in a supervisor workflow can read and write.
    Agents publish intermediate findings; others subscribe and consume relevant entries.
    """
    topic = "cuga.supervisor.blackboard"

    async def post(self, key: str, value: dict): ...
    async def subscribe(self, key_prefix: str) -> AsyncIterator[dict]: ...
```

This fills the gap in current CugaSupervisor where sub-agents cannot share intermediate context without explicit wiring.

**4d. Supervisor Configuration**

```python
# In deployment config — tells CugaSupervisor to use Kafka transport
supervisor = CugaSupervisor(
    agents=[...],
    transport=KafkaA2ATransport(kafka_config),   # runtime provides this
    blackboard=KafkaBlackboard(kafka_config),     # runtime provides this
)
```

The `KafkaA2ATransport` and `KafkaBlackboard` classes live in the runtime package. The transport interface they implement lives in CUGA core (already exists). No other CUGA core changes.

### Runtime files added
```
runtime/
  supervisor_transport.py
  blackboard.py
```

### Milestone
CugaSupervisor fans out a due diligence task to 4 parallel worker CUGA instances via Kafka. One worker fails mid-task. Kafka rebalances the partition to a healthy instance. The supervisor receives all 4 results and aggregates — with no bespoke failure handling code.

---

## Phase 5 — Audit and Governance
**What:** Immutable, replayable record of every CUGA invocation in Kafka.
**Prerequisite:** Phase 1.
**CUGA core changes:** One `ActivityEmitter` Protocol hook in `ActivityTracker` — opt-in, no external dependencies.

### What to build

**5a. The ActivityEmitter Hook (CUGA core — the one exception)**

This is the only change to CUGA core across all phases. It adds observability extensibility without adding any infrastructure dependency:

```python
# In CUGA core: src/cuga/backend/activity_tracker/tracker.py

class ActivityEmitter(Protocol):
    """Implement this in the runtime layer. CUGA core has no Kafka dependency."""
    def emit(self, step: ActivityStep) -> None: ...

class ActivityTracker:
    def __init__(self, ..., emitter: ActivityEmitter | None = None):
        self.emitter = emitter  # None by default — no behavior change

    def record_step(self, step: ActivityStep):
        # existing behavior: write to markdown, etc. — unchanged
        if self.emitter:
            self.emitter.emit(step)  # runtime receives this if registered
```

Without a registered emitter, CUGA behaves exactly as it does today. This is purely additive.

**5b. Kafka Audit Emitter (Runtime)**

```python
# runtime/audit.py

class KafkaActivityEmitter:
    """Lives in the runtime. Never imported by CUGA core."""
    def emit(self, step: ActivityStep):
        event = AuditEvent(
            thread_id=step.thread_id,
            task_id=step.task_id,
            step_type=step.type,       # tool_call, llm_call, policy_check, hitl, answer
            agent_name=step.agent,
            persona=step.persona,
            input=step.input,
            output=step.output,
            policy_applied=step.policy,
            duration_ms=step.duration_ms,
            timestamp=datetime.utcnow(),
        )
        self.producer.produce("cuga.audit.executions", key=step.thread_id, value=event.json())
```

**5c. Audit Event Schema**

```python
class AuditEvent(BaseModel):
    thread_id: str
    task_id: str
    step_type: Literal["tool_call", "llm_call", "policy_check", "hitl_request", "final_answer"]
    agent_name: str
    persona: str
    input: dict
    output: dict
    policy_applied: str | None
    duration_ms: int
    timestamp: datetime
    user_id: str | None
```

**5d. Audit Topic Configuration**

```yaml
# runtime/config/kafka.yaml (extend)

audit:
  topic: cuga.audit.executions
  retention_ms: -1                  # indefinite (configure per regulatory requirement)
  cleanup_policy: delete            # or compact for deduplication
  field_encryption:
    enabled: true
    pii_fields: [input.user_data, output.answer]   # Confluent field-level encryption
  access_control:
    read: [compliance-team, audit-service]
    write: [cuga-runtime]
```

**5e. Replay for Debugging**

Because CUGA's execution trace is now in Kafka, any run can be replayed: re-consume from the thread's first offset and pipe to a debug consumer. Pair with LangGraph's checkpointer (already in CUGA) for full state reconstruction.

### Files

```
CUGA core (one change):
  src/cuga/backend/activity_tracker/tracker.py   add ActivityEmitter Protocol + hook

Runtime (new):
  runtime/audit.py
  runtime/schemas.py (extend: AuditEvent)
  runtime/config/kafka.yaml (extend: audit section)
```

### Milestone
Every CUGA tool call, policy decision, and final answer appears in `cuga.audit.executions`. Compliance team reconstructs a past agent run from the Kafka log without touching the application.

---

## Phasing Summary

| Phase | What it builds | CUGA core change | Recommended order |
|---|---|---|---|
| **1 — Kafka Worker** | Consumer/producer wrapper calling CUGA REST API | None | First |
| **2 — Event-Triggered Personas** | Domain topics, event translator, persona-aware workers | None | Second |
| **3 — Streaming Knowledge** | Continuous embedding pipeline into shared vector store | None | Third or parallel with 4 |
| **4 — Multi-Agent Kafka Transport** | Kafka-backed CugaSupervisor coordination | None (uses existing transport interface) | Third or parallel with 3 |
| **5 — Audit + Governance** | Immutable execution log with full fidelity | `ActivityEmitter` Protocol hook (opt-in) | Can run in parallel after Phase 1 |

**Recommended sequence:** 1 → 2 → 3 and 4 in parallel → 5 in parallel after 1

Rationale: Phases 1 and 2 together demonstrate the business case — autonomous event-triggered operation — with the smallest footprint. Phases 3 and 4 deliver scale and quality. Phase 5 can start as soon as Phase 1 is live (input/output audit requires no CUGA core hook; the `ActivityEmitter` addition is done when full fidelity is needed).

---

## What CUGA Core Does Not Change

Every component of CUGA core remains exactly as it is today:

- `CugaLiteSubgraph` — the core reasoning loop
- LangGraph `StateGraph` and graph compilation
- Policy system (Intent Guard, Tool Approval, Playbook, Output Formatter)
- Tool providers (OpenAPI, MCP, LangChain)
- HITL interrupt mechanism
- Variable management and state persistence
- REST API and Python SDK (remain fully functional alongside the runtime)
- Benchmark-validated reasoning quality

The CUGA Streaming Runtime is a new ring around CUGA's existing core. It calls in; it does not reach inside.

---

*Based on: Confluent "A Guide to Event-Driven Design for Agents and Multi-Agent Systems" (2025) + CUGA codebase at /Users/anu/Documents/GitHub/cuga-agent-apr10*
