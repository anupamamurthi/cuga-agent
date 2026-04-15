# CUGA: Architecture & Playground

> A configurable generalist agent, a pattern library of real-world apps,
> and an interactive playground — end to end.

---

## 1. What Is CUGA?

CUGA is a **high-performance generalist agent** built on LangGraph.
It was built to answer a hard question: *how do you take a frontier LLM and
turn it into something reliable enough for production use across arbitrary
domains?*

The answer is a layered architecture that separates reasoning (the agent
brain) from configuration (tools + skills + policies) from execution
(the runtime). Any application built with CUGA follows this separation,
which is why every demo app in this repo looks structurally similar even
though the domains are completely different.

---

## 2. Layered Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     CUGA PLAYGROUND  (UI)                        │
│   Interactive browser-based interface for exploring CUGA         │
│   ─ Browse demo apps     ─ See architecture diagrams            │
│   ─ Try live agents      ─ Inspect use cases & patterns         │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP / WebSocket
┌───────────────────────────▼─────────────────────────────────────┐
│                     DEMO APP LAYER  (12 apps)                    │
│                                                                  │
│  Smart Todo │ Travel Planner │ Stock Alert │ Video Q&A │ ...     │
│                                                                  │
│  Each app = FastAPI server + CugaAgent(tools, skills)            │
│             + optional background pipeline                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │ CugaAgent SDK
┌───────────────────────────▼─────────────────────────────────────┐
│                     CONFIGURATION LAYER                          │
│                                                                  │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────────┐  │
│  │   Tools   │  │  Skills   │  │ Policies  │  │   Memory    │  │
│  │ @tool fns │  │  *.md     │  │ 5 types   │  │ thread_id   │  │
│  │ OpenAPI   │  │ reasoning │  │ guards,   │  │ checkpointer│  │
│  │ MCP srvrs │  │ rules,    │  │ playbooks │  │ multi-turn  │  │
│  │           │  │ formats   │  │ approvals │  │             │  │
│  └───────────┘  └───────────┘  └───────────┘  └─────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                     CUGA AGENT  (the brain)                      │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │               LangGraph Execution Engine                 │    │
│  │                                                         │    │
│  │  Intent     Task          Tool          Planning        │    │
│  │  Analysis ─► Decomp. ──► Dispatch ──► Reasoning        │    │
│  │                │             │                          │    │
│  │           Human-in-Loop  Save/Reuse                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Reasoning modes:  fast ──────── balanced ──────── deep         │
│  Model providers:  Anthropic │ OpenAI │ WatsonX │ Ollama        │
│  Observability:    OpenLit tracing │ LangFuse instrumentation    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. The CUGA Agent (Core Layer)

The agent is a **LangGraph state machine** with purpose-built nodes:

```
      ┌──────────────┐
      │  User Input  │
      └──────┬───────┘
             │
      ┌──────▼───────────────────────────────────────┐
      │  Intent Analysis  (policy: intent guards)     │
      └──────┬───────────────────────────────────────┘
             │
      ┌──────▼───────────────────────────────────────┐
      │  Task Decomposition  (complex → subtasks)     │
      └──────┬───────────────────────────────────────┘
             │
      ┌──────▼───────────────────────────────────────┐
      │  Tool Dispatch  (calls @tool / API / browser) │
      │                                              │
      │  ← policy: tool guides inject domain context │
      │  ← policy: tool approvals gate risky calls   │
      └──────┬───────────────────────────────────────┘
             │
      ┌──────▼───────────────────────────────────────┐
      │  Reasoning / Synthesis  (mode: fast|deep)     │
      └──────┬───────────────────────────────────────┘
             │
      ┌──────▼───────────────────────────────────────┐
      │  Output  (policy: output formatters applied)  │
      └──────────────────────────────────────────────┘
```

**Five policy types** give you fine-grained control over any agent:

| Policy Type | What It Does |
|---|---|
| **Intent Guards** | Block disallowed requests before the agent acts |
| **Playbooks** | Standardize how multi-step workflows are executed |
| **Tool Approvals** | Require human confirmation before risky tool calls |
| **Tool Guides** | Inject domain knowledge before specific tools run |
| **Output Formatters** | Enforce consistent response structure |

**Reasoning modes** let you tune cost vs. quality per deployment:

```
  fast ◄────────────────────────────────────► deep
  (heuristic routing,       (full task decomposition,
   single-pass tools,        multi-step planning,
   low latency)              highest accuracy)
```

---

## 4. The Configuration Layer

Every app configures a CUGA agent with three things:

### 4.1 Tools

```python
@tool
def get_stock_price(ticker: str) -> str:
    """Returns current price for a given stock ticker."""
    ...

agent = CugaAgent(tools=[get_stock_price, get_crypto_price])
```

Tools are plain Python functions, OpenAPI specs, or MCP server endpoints.
The agent decides *which* tools to call, *in what order*, and *how many times*
— you just define what's available.

### 4.2 Skills

```
.cuga/
  skills/
    reasoning.md      ← domain rules (when to classify a request as X)
    output_format.md  ← how to structure the response
    guardrails.md     ← what the agent must never do
```

Skills are Markdown files injected into the system prompt. They carry
institutional knowledge: classification rules, date-parsing conventions,
priority logic, output schemas. They make behavior reproducible without
fine-tuning.

### 4.3 Policies

Policies are runtime configurations that sit *outside* the prompt:
intent guards, approval gates, tool annotations. They enforce compliance
and consistency across every invocation.

---

## 5. The Demo App Layer

Twelve production-quality apps, each illustrating a different integration
pattern. They are not toys — each one solves a real workflow.

```
demo_apps/
├── smart_todo/         ─ NLP todo manager, due-date reminders, email digests
├── travel_agent/       ─ 5-source itinerary planner (Wikipedia, weather, POIs)
├── video_qa/           ─ Whisper transcription → ChromaDB → timestamped Q&A
├── voice_journal/      ─ Audio journal, weekly email digest
├── web_researcher/     ─ Scheduled Tavily search, results via email
├── stock_alert/        ─ Real-time price queries + threshold background alerts
├── server_monitor/     ─ System health dashboard + DevOps chat diagnostics
├── crm/                ─ Contact/deal management via natural language
├── deck_forge/         ─ Presentation generator from PDF/text inputs
├── dev_tools/          ─ Dev environment management via conversation
├── drop_summarizer/    ─ PDF drop-in summarizer
└── newsletter/         ─ RSS-to-email newsletter with curation
```

### How They Fit into the Architecture

Every demo app is the same structural pattern applied to a different domain:

```
┌──────────────────────────────────────────────────────┐
│  Demo App (e.g. Smart Todo)                           │
│                                                      │
│  ┌────────────────┐   ┌────────────────────────────┐ │
│  │  FastAPI Server│   │  Background Pipeline        │ │
│  │  ─ POST /ask   │   │  (optional)                 │ │
│  │  ─ GET  /todos │   │  ─ Asyncio loop (reminders) │ │
│  │  ─ Static HTML │   │  ─ Cron trigger (digest)    │ │
│  └───────┬────────┘   │  ─ File watcher (inbox)     │ │
│          │            └────────────────────────────┘ │
│  ┌───────▼────────────────────────────────────────┐   │
│  │  CugaAgent(                                    │   │
│  │    tools=[save_todo, list_todos, mark_done],   │   │
│  │    skills=".cuga/skills/todo_reasoning.md"     │   │
│  │  )                                             │   │
│  └───────────────────────────────────────────────┘   │
│                                                      │
│  Storage: SQLite  │  State: thread_id memory          │
└──────────────────────────────────────────────────────┘
```

**Pattern catalog across the 12 apps:**

| Pattern | Apps That Use It |
|---|---|
| On-demand query (request/response) | Travel Planner, Video Q&A, CRM, Deck Forge |
| Persistent state + CRUD | Smart Todo, Voice Journal, CRM |
| Background monitoring + alerts | Stock Alert, Server Monitor, Web Researcher |
| Scheduled digests | Smart Todo, Voice Journal, Newsletter |
| File/media input processing | Video Q&A, Voice Journal, Deck Forge, Drop Summarizer |
| Multi-source data synthesis | Travel Planner (5 APIs), Web Researcher |
| Allowlisted system commands | Server Monitor, Dev Tools |

---

## 6. The Playground (UI)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CUGA PLAYGROUND                               │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Home        │  │  Demo Apps   │  │  Architecture Diagrams│  │
│  │              │  │              │  │                      │  │
│  │  What CUGA   │  │  12 live     │  │  How each pattern    │  │
│  │  is and why  │  │  apps you    │  │  works, with ASCII   │  │
│  │  it matters  │  │  can run     │  │  and system diagrams │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Building    │  │  Use Cases   │  │  Features            │  │
│  │  Blocks      │  │              │  │                      │  │
│  │              │  │  50+ real    │  │  Policy system,      │  │
│  │  Tools,      │  │  world apps  │  │  reasoning modes,    │  │
│  │  skills,     │  │  with setup  │  │  tool integrations   │  │
│  │  policies,   │  │  commands    │  │                      │  │
│  │  channels    │  │  and env     │  │                      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                  │
│  Built with: React + TypeScript + Vite + Tailwind                │
│  Start with: npm run dev  (from /ui)                             │
└─────────────────────────────────────────────────────────────────┘
```

The playground is the **front door to CUGA**. Its purpose is to answer
the first-time user's three questions before they write a single line of code:

1. *What can this actually do?* → Use Cases catalog (50+ patterns, filtered by domain, complexity, tools used)
2. *How does it work?* → Architecture page (diagrams, building blocks, data flow)
3. *Can I try it right now?* → Demo Apps page (each app has a launch command; clicking "run" gives you a live agent)

### Why the Playground Is the Right Frame

Other agent frameworks show you a README with a code snippet. The problem
is that reading code is not the same as *understanding why a design decision
was made*. The playground closes that gap:

- **For evaluators:** The Use Cases page answers "does this cover my domain?"
  without requiring them to read source files.
- **For developers:** The Architecture page explains the pattern before they
  implement it. The Building Blocks page shows the exact SDK calls.
- **For non-technical stakeholders:** The demo apps run in a browser. No local
  setup required if deployed. The conversation UI is self-explanatory.

The playground does not *replace* documentation — it is the documentation,
made interactive.

---

## 7. Full System Picture

```
                        USER
                          │
                          │  opens browser
                          ▼
            ┌─────────────────────────┐
            │   CUGA PLAYGROUND (UI)  │
            │   React SPA             │
            │   npm run dev           │
            └──────────┬──────────────┘
                       │
         ┌─────────────┼──────────────┐
         │             │              │
         ▼             ▼              ▼
  [Browse Use    [Read How      [Launch a
   Cases &        It Works]      Demo App]
   Patterns]                        │
                                    │ HTTP to app port
                                    ▼
                          ┌──────────────────┐
                          │   Demo App       │
                          │   FastAPI server │
                          │   port: 8xxx     │
                          └────────┬─────────┘
                                   │
                                   │ CugaAgent SDK
                                   ▼
                          ┌──────────────────┐
                          │   CugaAgent      │
                          │   ─ tools        │
                          │   ─ skills       │
                          │   ─ policies     │
                          │   ─ memory       │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │  LangGraph       │
                          │  Execution       │
                          │  Engine          │
                          └────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────┐
              ▼                    ▼                 ▼
     External APIs          Browser (Playwright)   Code Sandbox
     (weather, stocks,      (form filling,         (Python exec,
      search, calendar)      page understanding)    safe execution)
```

---

## 8. Design Principles

**Separation of concerns is load-bearing.**
The agent brain does not know what domain it's in. The configuration layer
(tools + skills + policies) is what makes it a todo manager vs. a stock
alert vs. a travel planner. This means swapping domains requires changing
configuration, not rewriting agent logic.

**Skills encode institutional knowledge without fine-tuning.**
A skill file is a contract between the developer and the agent: "here is
how you should classify this input, here is what output format I expect,
here is what you should never do." This is cheaper, faster to iterate, and
more auditable than prompt engineering buried in application code.

**Policies enforce compliance at the framework level.**
Intent guards, approval gates, and output formatters run outside the main
reasoning loop. This means they cannot be bypassed by a sufficiently
creative user prompt — they are structural, not conversational.

**Demo apps are reference implementations, not tutorials.**
Each demo app is production-quality: it handles persistence, error cases,
background tasks, and real external APIs. The point is not to show that
CUGA *can* do something — it is to show exactly *how* you would build it
in a way you could ship.

**The playground is the product story.**
A capable agent framework that users cannot explore is a library, not a
product. The playground converts CUGA from a framework into an experience:
users can go from "I've never heard of this" to "I have a live agent
answering questions about my domain" in under ten minutes.

---

## 9. Getting Started

### Run the Playground

```bash
cd /Users/anu/Desktop/cuga++/ui
npm install
npm run dev
# Opens at http://localhost:5173
```

### Run a Demo App

```bash
cd docs/examples/demo_apps/travel_agent
pip install -r requirements.txt
uvicorn app:app --port 8090
# Chat UI at http://localhost:8090
```

### Build Your Own App

```python
from cuga.sdk import CugaAgent
from langchain_core.tools import tool

@tool
def my_tool(query: str) -> str:
    """What this tool does."""
    return do_something(query)

agent = CugaAgent(
    tools=[my_tool],
    skills_dir=".cuga/skills/"
)

result = await agent.invoke("user message", thread_id="session-1")
```

---

*CUGA: configurable, generalist, production-ready.*
