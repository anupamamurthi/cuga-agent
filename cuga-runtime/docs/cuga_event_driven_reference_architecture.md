# CUGA Event-Driven Reference Architecture

*Synthesized from:*
- *"A Guide to Event-Driven Design for Agents and Multi-Agent Systems" — Sean Falconer, Confluent (2025)*
- *"Building an Event-Driven Agentic AI System with Apache Kafka and watsonx Orchestrate" — Ahmed Azraq & Moisés Domínguez, IBM (2025)*

---

## The Architecture

```
 ┌─────────────────────────────── CONFLUENT PLATFORM ──────────────────────────────────┐
 │                                                                                       │
 │  Event Sources          Kafka Topics          ksqlDB             Kafka Connect        │
 │  ─────────────          ────────────          ──────             ─────────────        │
 │                                                                                       │
 │  CRM / Salesforce ───▶  transactions    ───▶  LIVE STATE   ◀──  Confluence           │
 │  ERP / Inventory  ───▶  inventory            TABLES             SharePoint           │
 │  IoT / Sensors    ───▶  alerts          ───▶  (per-entity        Salesforce           │
 │  Monitoring       ───▶  compliance           current state)      Slack / DBs          │
 │  Web / Clickstream───▶  documents                                                     │
 │                                                    │                    │              │
 └────────────────────────────────────────────────────┼────────────────────┼─────────────┘
                    task events                        │ MCP tool queries   │ doc events
                         │                            │                    ▼
                         │                            │           ┌─────────────────┐
                         │                            │           │  Streaming      │
                         │                            │           │  Knowledge      │
                         │                            │           │  Pipeline       │
                         │                            │           │  (embeddings)   │
                         │                            │           └────────┬────────┘
                         │                            │                    │ continuous
                         ▼                            │                    ▼
 ┌────────────────────────────────────┐              │           ┌─────────────────┐
 │      CUGA STREAMING RUNTIME        │              │           │  Vector Store   │
 │                                    │              │           │  (fresh context)│
 │  ┌──────────────────────────────┐  │              │           └────────┬────────┘
 │  │       Kafka Worker           │  │              │                    │
 │  │  ┌──────────────────────┐   │  │              │                    │
 │  │  │  Event Translator    │   │  │              │                    │
 │  │  │  (event → prompt)    │   │  │              │                    │
 │  │  └──────────────────────┘   │  │              │                    │
 │  │  offset commit on success    │  │              │                    │
 │  │  dead-letter on failure      │  │              │                    │
 │  └──────────────┬───────────────┘  │              │                    │
 │                 │  REST call        │              │                    │
 └─────────────────┼───────────────────┘              │                    │
                   │                                   │                    │
                   ▼                                   │                    │
 ┌─────────────────────────────────────────────────────────────────────────────────────┐
 │                                   CUGA CORE                                          │
 │                                                                                       │
 │  ┌─────────────────────────────────────────────────────────────────────────────┐     │
 │  │                           CugaSupervisor                                    │     │
 │  │           Routing  ·  Planning  ·  Task Delegation  ·  Aggregation          │     │
 │  └────────────────────────────────┬────────────────────────────────────────────┘     │
 │                                   │                                                   │
 │          ┌────────────────────────┼───────────────────────┐                          │
 │          ▼                        ▼                        ▼                          │
 │  ┌───────────────┐       ┌────────────────┐      ┌────────────────┐                  │
 │  │  Live State   │       │  Knowledge     │      │  Action        │                  │
 │  │  Agent        │       │  Agent         │      │  Agent         │                  │
 │  │               │       │                │      │                │                  │
 │  │  Routing      │       │  Routing       │      │  Routing       │                  │
 │  │  Reasoning    │       │  Reasoning     │      │  Reasoning     │                  │
 │  │  Tool calling │       │  RAG           │      │  Tool calling  │                  │
 │  │  Planning     │       │  Planning      │      │  Code-act      │                  │
 │  └───────┬───────┘       └───────┬────────┘      └───────┬────────┘                  │
 │          │                       │                        │                           │
 │  Policy  │  Intent Guard ────────┴────────────────────────┘                          │
 │  System  │  Tool Approval                                                             │
 │          │  Playbook                         LLM (Claude / OpenAI / etc.)            │
 │          │  Output Formatter                 ↑  ↑  ↑                                 │
 │          └───────────────────────────────────┘  │  └──────────────────────────────   │
 │                                                  │                                    │
 └──────────────────────────────────────────────────┼────────────────────────────────────┘
                                                     │
                    ┌────────────────────────────────┼──────────────────────────┐
                    ▼                                ▼                           ▼
            ┌──────────────┐               ┌─────────────────┐        ┌──────────────────┐
            │    ksqlDB    │               │  Vector Store   │        │  External APIs   │
            │  (live state │               │  (fresh docs,   │        │  (CRM, ERP,      │
            │   tables)    │               │   embeddings)   │        │   calendars,     │
            │  via MCP     │               │  knowledge_     │        │   databases)     │
            │  tool        │               │  search tool    │        │  OpenAPI tools   │
            └──────────────┘               └─────────────────┘        └──────────────────┘
                    ▲                                ▲
                    └──── both fed by Confluent Platform in real time ────────────────────┘
```

---

## What's New: Two Data Paths, One Agent

The IBM tutorial introduces a distinction that sharpens the architecture significantly. There are two fundamentally different kinds of data an event-driven agent needs, and they require different infrastructure:

| | Live State | Document Knowledge |
|---|---|---|
| **What it is** | Current operational facts: inventory level, account status, sensor reading | Explanatory / procedural content: product catalog, runbooks, policies |
| **How it's stored** | ksqlDB materialized table (continuously updated from Kafka streams) | Vector store (continuously updated via embedding pipeline) |
| **How agent accesses it** | MCP tool → ksqlDB query | `knowledge_search` tool → vector store semantic search |
| **Latency** | Milliseconds — point query on current state | Milliseconds — ANN vector search |
| **When agent uses it** | First — check live state | Second — conditionally, when live state triggers it |

The IBM retail example makes this concrete:

```
Store associate asks: "Do you have LAPTOP-DELL-XPS-15 in MallofEgypt?"

Step 1:  Live State Agent calls get_sku_availability via MCP
         → ksqlDB query: SELECT available_qty FROM INVENTORY_AVAILABILITY
                         WHERE sku='LAPTOP-DELL-XPS-15' AND branch='MallofEgypt'
         → result: 0 (out of stock)

Step 2:  Agent conditionally triggers Knowledge Agent (Agentic RAG)
         → "out of stock — find substitutes"
         → knowledge_search on product catalog documents
         → retrieves substitute laptops with specs

Step 3:  Agent synthesizes: "Out of stock. Consider these alternatives: ..."
```

The key: **the agent decides at runtime whether to invoke RAG** — only when live state signals it's needed. This is "agentic RAG" — RAG as a conditional tool call, not a fixed pipeline.

---

## How This Maps to CUGA

CUGA already has both capabilities in its tool system. The runtime adds the streaming infrastructure that keeps both data paths current.

### Live State via ksqlDB + MCP

CUGA natively supports MCP tools. Expose ksqlDB as an MCP server — CUGA's Live State Agent calls `get_live_state(entity, key)` and gets the Kafka-backed current value without any awareness of the underlying stream infrastructure.

```python
# ksqlDB MCP Tool (in runtime, not CUGA core)
# Registered with CUGA as an MCP server at startup

@mcp_tool
def get_sku_availability(sku: str, branch: str) -> dict:
    """Query current inventory for a SKU at a branch."""
    return ksqldb_client.query(
        f"SELECT * FROM INVENTORY_AVAILABILITY "
        f"WHERE sku='{sku}' AND branch='{branch}' EMIT CHANGES LIMIT 1;"
    )

@mcp_tool
def get_account_health(account_id: str) -> dict:
    """Query current account health metrics."""
    return ksqldb_client.query(
        f"SELECT * FROM ACCOUNT_HEALTH_STATE WHERE account_id='{account_id}';"
    )
```

CUGA calls these like any other tool. The MCP server is deployed by the runtime — not part of CUGA core.

### Document Knowledge via Streaming Embeddings

The runtime's streaming knowledge pipeline continuously updates the same vector store CUGA's `knowledge_search` tool already reads from. CUGA retrieves fresh context automatically — no agent code changes.

### ksqlDB: What It Adds That Plain Kafka Does Not

This is the element absent from the Confluent paper but central to the IBM tutorial. Without ksqlDB, agents face two bad options:
1. Consume the entire Kafka topic to reconstruct current state — expensive and slow
2. Query a separate database that may be stale

ksqlDB solves this by maintaining a **continuously updated materialized view** of Kafka streams:

```sql
-- ksqlDB stream: raw inventory events
CREATE STREAM INVENTORY_TRANSACTIONS (
    sku VARCHAR, branch VARCHAR, quantity_delta INT, timestamp BIGINT
) WITH (KAFKA_TOPIC='inventory.transactions', VALUE_FORMAT='JSON');

-- ksqlDB table: current state per SKU per branch (auto-updated from stream)
CREATE TABLE INVENTORY_AVAILABILITY AS
    SELECT sku, branch, SUM(quantity_delta) AS available_quantity
    FROM INVENTORY_TRANSACTIONS
    GROUP BY sku, branch
    EMIT CHANGES;
```

Now `INVENTORY_AVAILABILITY` is always current — every Kafka event updates it instantly. The agent queries a table, not a stream. Clean, fast, and correct.

**In the CUGA Streaming Runtime**, ksqlDB tables are exposed as MCP tools. One ksqlDB cluster per enterprise domain (inventory, accounts, compliance state, ops metrics) gives CUGA agents precise, millisecond-fresh answers to state queries.

---

## The Conditional Agentic RAG Pattern

The IBM tutorial's most important architectural insight: **live state and document knowledge are not two separate pipelines — they are two tools a single agent chooses between dynamically**.

```
Agent receives task
        │
        ▼
Query live state (ksqlDB via MCP)
        │
        ├── state is definitive answer ──▶ respond directly
        │
        └── state triggers further reasoning
                │
                ├── need explanatory context ──▶ knowledge_search (agentic RAG)
                │
                └── need to take action ──▶ API tool call (CRM, ERP, calendar)
```

This is different from fixed-pipeline RAG (always retrieve, always inject). The agent decides. CUGA's code-act reasoning loop is exactly the right architecture for this — it generates code that calls whichever tools are appropriate given the current state of the task.

**Example mapping for different CUGA personas:**

| Persona | Live State Query (ksqlDB) | Conditional RAG Trigger | RAG Content |
|---|---|---|---|
| **Retail / Ops** | inventory levels, order status | out of stock → find substitutes | product catalog |
| **IT Ops** | service error rate, deployment state | anomaly detected → investigate | runbooks, postmortems |
| **Customer Success** | account usage metrics, health score | usage decline → outreach brief | account history, playbooks |
| **Compliance** | current regulatory status, flag state | new flag → gap analysis | policy documents, regulations |
| **Finance** | transaction anomaly score, threshold state | anomaly above threshold → investigate | audit policies, historical patterns |

---

## The Full Data Flow, Step by Step

Using the retail example as a concrete walkthrough of the full architecture:

```
1. KAFKA STREAM
   POS system fires: { sku: "LAPTOP-DELL-XPS-15", branch: "MallofEgypt",
                       quantity_delta: -1, type: "sale" }
   → published to: inventory.transactions topic

2. KSQLDB MATERIALIZATION (automatic, continuous)
   INVENTORY_AVAILABILITY table updates:
   { sku: "LAPTOP-DELL-XPS-15", branch: "MallofEgypt", available_quantity: 0 }

3. KAFKA CONNECT (parallel, continuous)
   Product catalog updated in Confluence
   → Kafka Connect picks up change → document event on enterprise.docs.confluence
   → Streaming Knowledge Pipeline re-embeds updated catalog entry
   → Vector store updated

4. RUNTIME KAFKA WORKER (event-triggered path)
   OR: human query arrives via UI → runtime translates → CugaTaskEvent
   "Do you have LAPTOP-DELL-XPS-15 in MallofEgypt?"

5. CUGA CORE — CugaSupervisor
   Routes task to: Live State Agent

6. LIVE STATE AGENT
   Calls MCP tool: get_sku_availability("LAPTOP-DELL-XPS-15", "MallofEgypt")
   → ksqlDB query → result: available_quantity = 0

7. CUGA CORE — CugaSupervisor (conditional delegation)
   Live state = 0 → delegates to: Knowledge Agent
   "Find substitute laptops for LAPTOP-DELL-XPS-15"

8. KNOWLEDGE AGENT
   Calls knowledge_search("laptop substitutes for DELL-XPS-15 specs")
   → vector store semantic search → returns 3 alternatives with specs

9. POLICY SYSTEM
   Output Formatter applies response template
   Tool Approval: read-only — no approval needed
   Intent Guard: passes

10. FINAL ANSWER
    "LAPTOP-DELL-XPS-15 is out of stock at Mall of Egypt.
     Alternatives: LAPTOP-HP-SPECTRE-X360, LAPTOP-LENOVO-THINKPAD-X1,
     LAPTOP-ASUS-ZENBOOK-14 [with specs]"

11. AUDIT LOG (Kafka)
    All steps recorded in cuga.audit.executions — reconstructible and compliant
```

---

## What Each Layer Does and Who Owns It

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CONFLUENT PLATFORM                                                          │
│  Owned by: infrastructure / data engineering team                           │
│                                                                              │
│  • Kafka topics — raw event streams from all enterprise systems             │
│  • ksqlDB — materialized state tables, continuously updated                 │
│  • Kafka Connect — 120+ connectors pulling from enterprise sources          │
│  • Schema Registry — enforces event schema contracts                        │
│  • Stream Governance — lineage, compliance, field-level encryption          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│  CUGA STREAMING RUNTIME                                                      │
│  Owned by: AI platform / agent infrastructure team                          │
│                                                                              │
│  • Kafka Worker — consumes task events, calls CUGA REST API                 │
│  • Event Translator — converts structured events to natural language tasks  │
│  • Streaming Knowledge Pipeline — Flink embeddings → vector store           │
│  • ksqlDB MCP Tools — expose live state tables as agent tools               │
│  • Supervisor Kafka Transport — elastic multi-agent coordination            │
│  • Audit Interceptor — publishes execution trace to Kafka audit topic       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ REST / MCP / vector store
┌──────────────────────────────────▼──────────────────────────────────────────┐
│  CUGA CORE                                                                   │
│  Owned by: AI/agent product team                                            │
│                                                                              │
│  • CugaSupervisor — orchestration, routing, task delegation                 │
│  • CugaLiteSubgraph — reasoning loop, planning, code-act execution          │
│  • Policy System — Intent Guard, Tool Approval, Playbook, Output Formatter  │
│  • Tool Providers — MCP, OpenAPI, LangChain tools                           │
│  • knowledge_search — semantic retrieval from vector store                  │
│  • HITL — human-in-the-loop interrupts and approvals                       │
│  • ActivityTracker — execution trace (emits via ActivityEmitter hook)       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│  DATA LAYER                                                                  │
│                                                                              │
│  • ksqlDB tables — live operational state (millisecond-fresh)               │
│  • Vector store — continuously updated document knowledge                   │
│  • External APIs — CRM, ERP, calendars, databases (via OpenAPI tools)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Architectural Principles (Synthesized from Both Sources)

**1. Agents query state, not streams.**
Agents should not consume Kafka topics directly. ksqlDB materializes streams into queryable tables. Agents call a tool. The streaming infrastructure is invisible to agent logic. *(IBM tutorial)*

**2. MCP is the clean agent-to-Kafka bridge.**
Expose ksqlDB tables as MCP tools. CUGA already supports MCP natively. No new agent code — just register the MCP server at deployment time. *(IBM tutorial)*

**3. RAG is conditional, not fixed.**
Document retrieval is a tool the agent chooses to call based on live state, not a mandatory first step. CUGA's code-act reasoning loop makes this natural — it decides which tools to call. *(IBM tutorial)*

**4. The runtime is not CUGA.**
All streaming infrastructure — Kafka worker, event translator, embedding pipeline, MCP tools, audit interceptor — lives outside CUGA core. CUGA core sees normal REST calls and tool invocations. It has no Kafka dependency. *(Confluent paper + our architecture decision)*

**5. Flink prepares data. CugaSupervisor coordinates agents.**
Flink handles enrichment and embedding at scale. CugaSupervisor handles agent orchestration. They operate at different layers. *(Confluent paper, clarified)*

**6. Events trigger context-aware action, not just responses.**
The value of event-driven is not faster responses — it is agents that act on the right data at the right moment without human initiation. *(Confluent paper)*

---

## Comparison: IBM watsonx Pattern vs. CUGA

| Dimension | IBM watsonx Orchestrate (tutorial) | CUGA |
|---|---|---|
| **Agent framework** | watsonx Orchestrate | LangGraph / CugaLiteSubgraph |
| **Orchestrator** | Store Associate Agent | CugaSupervisor |
| **Live state tool** | MCP → ksqlDB (`get_sku_availability`) | MCP → ksqlDB (runtime-registered) |
| **Knowledge RAG** | Agentic RAG on enterprise documents | `knowledge_search` on vector store |
| **LLM** | GPT-OSS-120B via Groq | Claude / OpenAI / configurable |
| **Policy / governance** | — | Intent Guard, Tool Approval, Playbook |
| **HITL** | — | Native LangGraph interrupt mechanism |
| **Event trigger** | Human-initiated (store associate) | Human OR autonomous Kafka event |
| **Streaming infra** | Confluent Cloud + ksqlDB | Confluent Cloud + ksqlDB + Flink |
| **Code generation** | — | Code-act pattern (CUGA's differentiator) |
| **Multi-agent** | Two specialized agents | CugaSupervisor + N worker agents |

CUGA has everything the IBM tutorial demonstrates, plus autonomous event triggering, code-act reasoning, HITL, and a policy system. The runtime layer is what connects those capabilities to the enterprise event fabric.

---

*Sources:*
- *Confluent: "A Guide to Event-Driven Design for Agents and Multi-Agent Systems" (2025)*
- *IBM: "Building an Event-Driven Agentic AI System with Apache Kafka and watsonx Orchestrate" (2025)*
- *CUGA codebase: /Users/anu/Documents/GitHub/cuga-agent-apr10*
