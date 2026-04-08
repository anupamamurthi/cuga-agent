# Newsletter New — Architecture

## Vision: Event-Driven AI Pipelines via Natural Language

The goal is a system where:
- A **persistent host** owns all running pipelines (survives restarts, manages N pipelines)
- Users describe what they want in **plain English** — no YAML, no Python
- The system extracts a pipeline config, fills in defaults, and starts the pipeline immediately
- **Channel schemas** are the canonical description of what each channel type needs

---

## System overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SETUP PLANE  (chat.py — thin client, runs per session)                  │
│                                                                          │
│  User NL utterance                                                       │
│    └─► PipelineBuilder                                                   │
│          │  reads channel_schemas.py (required / optional per channel)   │
│          │  direct LLM call — no CugaAgent, no tool calling             │
│          │                                                               │
│          ├─► needs_clarification? → print question → loop               │
│          └─► ready? → flat config dict                                   │
│                {sources, keywords, schedule, email, ...}                 │
│                         │                                                │
│                         │  POST /runtime                                 │
│                         │  {"id": "...", "factory": "newsletter_new",    │
│                         │   "config": {...}}                             │
│                         │                                                │
│  Management commands (no LLM): list | status | stop                     │
│    └─► GET /runtime  |  GET /health  |  DELETE /runtime/{id}            │
└─────────────────────────┼───────────────────────────────────────────────┘
                          │ HTTP  (:18790)
┌─────────────────────────▼───────────────────────────────────────────────┐
│  CONTROL PLANE  (CugaHost — always-on daemon)                            │
│                                                                          │
│  cugahost start --factories newsletter_new.host_factories                │
│                                                                          │
│  REST API (Swagger at /docs):                                            │
│    GET  /health               → {status, port, pid, runtimes}           │
│    GET  /factories            → {factories: ["newsletter_new", ...]}     │
│    GET  /runtime              → [{id, factory, config, running, ...}]   │
│    GET  /runtime/{id}         → single runtime entry                    │
│    POST /runtime              → create / reconfigure runtime             │
│    PUT  /runtime/{id}         → reconfigure in place                    │
│    DELETE /runtime/{id}       → stop and remove                         │
│    POST /runtime/{id}/invoke  → one-shot agent call (bypass trigger)    │
│    POST /host/shutdown        → graceful stop                           │
│                                                                          │
│  ├── persists configs → ~/.cuga/host/runtimes.json                      │
│  ├── restores all runtimes on restart                                    │
│  └── looks up "newsletter_new" factory → builds CugaRuntime            │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │ builds + owns
┌─────────────────────────▼───────────────────────────────────────────────┐
│  EXECUTION PLANE  (CugaRuntime — one per active pipeline)                │
│                                                                          │
│  DATA (fills buffer)                       TRIGGER                       │
│  ┌──────────────────────────┐             ┌────────────────────────┐    │
│  │ RssChannel               │             │ CronChannel            │    │
│  │  polls feeds every       │─► buffer ◄──│  fires on schedule     │    │
│  │  poll_minutes            │  (deduped)  │  e.g. "0 8 * * *"     │    │
│  │  keyword filter          │             └───────────┬────────────┘    │
│  └──────────────────────────┘                         │                 │
│                                           buffer has items?             │
│                                             yes → invoke agent          │
│                                                       │                 │
│  REASONING                                            ▼                 │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │ CugaAgent                                                       │     │
│  │  in:  trigger message + buffered RSS items (JSON, ≤15 items)   │     │
│  │  uses: newsletter_curation skill                                │     │
│  │  out:  styled HTML newsletter                                   │     │
│  └────────────────────────────────────┬────────────────────────────┘    │
│                                       │ result.answer (HTML)             │
│  OUTPUT                               ▼                                 │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │ EmailChannel  →  SMTP → recipient inbox                         │     │
│  │ LogChannel    →  stdout (always active; only output if no email)│     │
│  └────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component roles

### `channel_schemas.py` — source of truth

Declarative definitions of what each channel type needs:

```
rss channel:
  REQUIRED: sources (list of RSS feed URLs)
  optional: keywords (filter terms), poll_minutes (default 15)

cron channel:
  REQUIRED: schedule (5-field cron expression)

email channel:
  REQUIRED: email (recipient address)
  optional: subject_prefix (default "CUGA Newsletter")
```

**Adding a new channel type** (e.g. `webhook`, `slack_in`) requires only
adding one entry here. `PipelineBuilder` reads this dict and generates its
system prompt automatically.

---

### `pipeline_builder.py` — NL → config extraction

**Why a direct LLM call, not CugaAgent?**

| Concern | CugaAgent | Direct LLM call |
|---|---|---|
| Task type | Reasoning + tool use | Structured JSON extraction |
| Latency | Higher (multiple round trips) | One HTTP call |
| History | Persisted to disk (checkpointer) | In-memory only |
| Complexity | LangGraph + tool registration | Plain message list |
| Use here | ✗ Overkill | ✓ Right tool |

CugaAgent is used for the **curation step** (reasoning over 15 RSS items to
write a newsletter). It is NOT used for config extraction.

**Multi-turn clarification** is handled with a simple in-memory list:
```python
history = []
history.append({"role": "user",      "content": utterance})
# call LLM → gets JSON {"status": "needs_clarification", "question": "..."}
history.append({"role": "assistant", "content": raw_json})
# user answers → process() called again → LLM has full context
```

No framework, no checkpointer, no state files.

---

### `host_factories.py` — wires channels to CugaRuntime

Uses `RuntimeFactory.declare()` — a declarative builder that avoids
imperative factory boilerplate:

```python
host.register_factory("newsletter_new", RuntimeFactory.declare(
    agent_fn  = lambda _: make_agent(),
    message   = "It's time to send the digest...",
    thread_id = "newsletter-new-digest",
    trigger   = CronChannel.from_config,    # reads config["schedule"]
    data      = [RssChannel.from_config],   # reads config["sources"], config["keywords"]
    output    = EmailChannel.from_config,   # reads config["email"]
    subject_prefix = "CUGA Newsletter",
    require_buffer = True,
))
```

The flat config dict from `PipelineBuilder` is passed directly to these
`from_config()` class methods — they know which keys to read.

---

### `chat.py` — thin client

Responsibilities:
1. Check that CugaHost is running (if not, print instructions and exit cleanly)
2. Call `PipelineBuilder.process()` in a loop until `status == "ready"`
3. POST the extracted config to `CugaHost /runtime`
4. Handle management commands (`list`, `stop`, `status`) without touching the LLM

`chat.py` does NOT embed or start the host. The host is external, always-on.

---

## Data flow: end to end

### Setup (runs once)

```
User: "watch arxiv for AI agents, email me@x.com every morning"

PipelineBuilder:
  LLM call #1 → {
    "status": "ready",
    "config": {
      "sources": ["https://arxiv.org/rss/cs.AI"],
      "keywords": ["AI agents", "agent", "agentic"],
      "poll_minutes": 15,
      "schedule": "0 8 * * *",
      "email": "me@x.com",
      "subject_prefix": "CUGA Newsletter"
    },
    "answer": "Monitoring arxiv for AI agents, daily digest to me@x.com."
  }

chat.py → POST /runtime {id: "newsletter-new-digest", factory: "newsletter_new", config: {...}}

CugaHost:
  looks up "newsletter_new" factory
  calls factory(config) → CugaRuntime(
    data=[RssChannel(sources=[arxiv], keywords=[...], poll_minutes=15)],
    trigger=CronChannel(schedule="0 8 * * *"),
    output=[EmailChannel(to="me@x.com"), LogChannel()]
  )
  runtime.launch()   ← starts all channel tasks
  saves config to runtimes.json
```

### Every 15 minutes (RssChannel)

```
RssChannel._poll_all()
  → fetches https://arxiv.org/rss/cs.AI
  → parses XML → 30 items
  → keyword filter ("AI agents") → 4 matched
  → ChannelBuffer.add([4 items])   (deduped by URL)

Buffer now has 4 items.
```

### Every day at 8am (CronChannel)

```
CronChannel fires → CugaRuntime._on_trigger("It's time to send the digest...")
  → buffer.read() → [4 items] (up to 15 capped)
  → full_message = trigger_message + JSON of items
  → CugaAgent.invoke(full_message, thread_id="newsletter-new-digest")
      → applies newsletter_curation skill
      → returns styled HTML
  → buffer.clear()
  → EmailChannel.deliver(html, metadata)
      → sends email to me@x.com
  → LogChannel.deliver(html, metadata)
      → prints to console
```

---

## Clarification flow

```
You:  "watch arxiv for AI agents"

PipelineBuilder → LLM → {
  "status": "needs_clarification",
  "question": "What schedule should the digest run on? (e.g. 'every morning at 8am')"
}

You:  "every 4 hours"

PipelineBuilder → LLM (with full conversation history) → {
  "status": "needs_clarification",
  "question": "Where should the digest be delivered? Email address, or log to console?"
}

You:  "log to console is fine"

PipelineBuilder → LLM → {
  "status": "ready",
  "config": {
    "sources": ["https://arxiv.org/rss/cs.AI"],
    "keywords": ["AI agents", "agent", "agentic"],
    "poll_minutes": 15,
    "schedule": "0 */4 * * *",
    "email": null,
    "subject_prefix": "CUGA Newsletter"
  },
  "answer": "Got it! Monitoring arxiv for AI agents, digest every 4 hours to console."
}
```

---

## Extending to new channels

To add a new channel type (e.g. `slack_in`, `webhook`, `webpage`):

1. **Add to `channel_schemas.py`**:
   ```python
   "webpage": {
       "role": "data",
       "description": "Polls a web page URL and fires when content changes",
       "required": ["url"],
       "optional": {"poll_minutes": {"default": 60, ...}},
       "extraction_hints": ['"watch IBM homepage" → url: "https://ibm.com"'],
   }
   ```

2. **Implement `WebPageChannel`** in `cuga-channels` (or import existing one)

3. **Register in `host_factories.py`**:
   ```python
   host.register_factory("webpage_newsletter", RuntimeFactory.declare(
       data=[WebPageChannel.from_config],
       ...
   ))
   ```

`PipelineBuilder` picks up the new schema entry automatically — no changes needed there.

---

## vs. newsletter (original)

| Aspect | `newsletter` | `newsletter_new` |
|---|---|---|
| Pipeline setup | YAML file edited by developer | NL utterance from user |
| Schema definition | Inline in `ChannelPlanner` params | Declarative `channel_schemas.py` |
| NL → config | `ChannelPlanner` (CugaAgent + tools) | `PipelineBuilder` (direct LLM call) |
| Host lifecycle | Can embed or connect externally | Always external (clean separation) |
| Curation agent | Same (CugaAgent + newsletter_curation skill) | Same |
| Multi-pipeline | No (one fixed pipeline ID) | Yes (each call creates/updates a pipeline) |
