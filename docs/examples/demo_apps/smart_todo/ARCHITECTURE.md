# Smart Todo — Architecture

## CUGA vs cuga++

### CUGA — the reasoning engine

CUGA's job is to think. It receives a message, reasons over it, calls tools
if needed, and produces output.  That is all.

In the smart-todo case CUGA is responsible for:

- **Interactive add** — classifying raw text, extracting fields, saving via `save_todo`
- **Digest** — reading todos via `list_todos`, composing an HTML digest, returning HTML
- **Reminders** — composing a brief HTML reminder body, returning HTML

CUGA is completely unaware of:
- How items get stored (SQLite, `store.py`)
- When the digest runs (cron schedule)
- When reminders fire (SQLite polling)
- Where output goes (email, stdout)

### cuga++ — the infrastructure layer

cuga++ owns everything around the agent:

| Responsibility | Component |
|---|---|
| Classify and persist user input | `save_todo` tool (called by CUGA) |
| Schedule and fire the daily digest | `CronChannel` (TriggerChannel) |
| Wake agent without a data buffer | `CugaRuntime(require_buffer=False)` |
| Poll SQLite for due reminders | `CugaWatcher` (`@source`) |
| Mark reminder done before invoke | `CugaWatcher` handler (`mark_done` before `invoke`) |
| Route agent output to delivery | `EmailChannel`, `LogChannel` (OutputChannel) |

The LLM never decides whether or where to deliver output.  Delivery is
deterministic — always fired after every successful agent invocation.

---

## Architecture diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║                          cuga++  (infrastructure)                    ║
║                                                                      ║
║   User (HTTP)                          TriggerChannels               ║
║  ┌─────────────────────┐              ┌──────────────────────────┐   ║
║  │ POST /add           │              │ CronChannel              │   ║
║  │                     │              │  schedule: "0 8 * * 1-5" │   ║
║  │  raw text           │              │  fires weekdays at 8am   │   ║
║  └──────────┬──────────┘              └────────────┬─────────────┘   ║
║             │ process(text)                        │ on_trigger(msg) ║
║             │                                       │                 ║
║             ▼                                       ▼                 ║
║       ┌─────────────────────────────────────────────────────────┐   ║
║       │                     CugaRuntime                         │   ║
║       │                (require_buffer=False)                   │   ║
║       │                                                         │   ║
║       │  No DataChannels — agent reads DB via list_todos tool   │   ║
║       │  Trigger always wakes agent regardless of buffer state  │   ║
║       └──────────────────────────┬──────────────────────────────┘   ║
║                                  │ agent.invoke(digest message)       ║
╚══════════════════════════════════╪══════════════════════════════════╝
                                   │
╔══════════════════════════════════╪══════════════════════════════════╗
║                    CUGA  (reasoning)                                 ║
║                                  ▼                                   ║
║                        ┌─────────────────┐                          ║
║                        │   CugaAgent     │                          ║
║                        │                 │                          ║
║        /add path ────► │  save_todo      │  ◄─── digest path        ║
║        (interactive)   │  list_todos     │       (scheduled)        ║
║                        │                 │                          ║
║                        │  returns HTML   │                          ║
║                        └────────┬────────┘                          ║
║                                 │ result.answer                     ║
╚═════════════════════════════════╪════════════════════════════════════╝
                                  │
╔═════════════════════════════════╪════════════════════════════════════╗
║                    cuga++  (delivery)                                 ║
║                                  ▼                                    ║
║              CugaRuntime fans out to all OutputChannels               ║
║                                                                       ║
║   ┌───────────────────────┐    ┌──────────────────────────────────┐  ║
║   │ EmailChannel          │    │ LogChannel                       │  ║
║   │ • sends HTML email    │    │ • prints to stdout               │  ║
║   │ • SMTP, deterministic │    │ • swap in for testing            │  ║
║   └───────────────────────┘    └──────────────────────────────────┘  ║
╚═══════════════════════════════════════════════════════════════════════╝


─── Reminder path (separate, per-item) ────────────────────────────────

╔══════════════════════════════════════════════════════════════════════╗
║                    cuga++  (CugaWatcher)                             ║
║                                                                      ║
║   @source  check_due_reminders  every_minutes=1                      ║
║     → list_due()  (SQLite query)                                     ║
║     → emits items only when list is non-empty                        ║
║     → [] silently dropped — agent never called                       ║
║                                                                      ║
║   @on  fire_reminders  when=len(items) > 0                           ║
║     → mark_done(id)  before invoke  (prevents double-fire)           ║
║     → agent.invoke("Compose reminder HTML for: …")  per item         ║
║     → EmailChannel.deliver(result.answer, {"subject": "⏰ …"})       ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## Why two separate runtimes

| | `CugaRuntime` (digest) | `CugaWatcher` (reminders) |
|--|--|--|
| Trigger source | Wall clock (CronChannel) | SQLite query |
| Data source | `list_todos` tool inside agent | `list_due()` Python call |
| Buffer needed? | No (`require_buffer=False`) | N/A — not using CugaRuntime |
| Pre-invoke action | None | `mark_done()` before `invoke()` |
| Agent calls per fire | 1 (batch digest) | 1 per due item |
| Delivery | EmailChannel in CugaRuntime | EmailChannel.deliver() in handler |
| Package | `cuga-channels` | `cuga-watcher` |

`CugaRuntime` with `require_buffer=False` is the right fit when the agent
fetches its own data via tools.  `CugaWatcher` is the right fit when a data
source must be checked first and the agent should be woken up per-item with
Python-level pre-processing (like marking done before invoke).

---

## cuga++ package roles

| Package | Role |
|---------|------|
| `cuga` (CugaAgent) | LangGraph ReAct loop. `save_todo` and `list_todos` tools. Built-in checkpointing per `thread_id`. |
| `cuga-skills` | `CugaSkillsPlugin` injects `skills/todo_reasoning.md` into every system prompt. |
| `cuga-channels` | `CugaRuntime` for digest pipeline. `CronChannel` for schedule. `EmailChannel` / `LogChannel` for delivery. |
| `cuga-watcher` | `CugaWatcher` pub-sub loop. `@source` polls `list_due()` every 60 s; `@on` handler fires agent per due reminder. |

---

## Data flows

### Saving a todo (interactive)

```
User types: "remind me to review the PR at 3pm"
  └─► POST /add → process(text)
      └─► CugaAgent + todo_reasoning.md:
          LLM → save_todo(content="Review the PR", type="reminder", due_date="...T15:00:00")
          └─► SQLite row, status='active'
          returns: {"todo": {...}, "reasoning": "Saved as reminder for 3 PM."}
```

### Daily digest (CronChannel → CugaRuntime → EmailChannel)

```
[Mon–Fri 8:00 AM]  CronChannel fires
  └─► CugaRuntime._on_trigger(digest_message)
      └─► agent.invoke(digest_message, thread_id="smart-todo-digest")
          └─► CugaAgent:
              list_todos(status='active')  → open items
              list_todos(status='done')    → completed items
              compose HTML digest
              return HTML
      └─► EmailChannel.deliver(html, metadata)   ← deterministic
          SMTP → inbox
```

### Reminder fires (CugaWatcher)

```
[every 60 s]  CugaWatcher._run_source()
  └─► check_due_reminders() → list_due()
      → [{id:1, content:"Review the PR", due_date:"...T15:00:00"}]  (non-empty → emitted)

CugaWatcher._dispatch()
  └─► fire_reminders(items):
      mark_done(1)                                    ← Python, before agent
      result = await agent.invoke('Compose reminder HTML for: "Review the PR"…')
        └─► CugaAgent returns HTML
      EmailChannel.deliver(html, {"subject": "⏰ Reminder: Review the PR"})
        └─► SMTP → inbox
```

---

## The app's only job

```python
# app.py startup — the entire infrastructure setup

_digest_runtime = make_digest_runtime()   # CronChannel → agent → EmailChannel
await _digest_runtime.launch()            # non-blocking, runs in background

_watcher = make_watcher()                 # CugaWatcher, polls SQLite every 60s
asyncio.create_task(_watcher.start())
```

```python
# agent.py — what make_digest_runtime() builds

CugaRuntime(
    agent=get_agent(),           # CugaAgent with save_todo + list_todos tools
    input_channels=[
        CronChannel(schedule="0 8 * * 1-5", message=digest_message),
    ],
    output_channels=[
        EmailChannel.from_env(subject_prefix="📋 Smart Todo Digest", to_env_var="DIGEST_TO"),
        LogChannel(),
    ],
    thread_id="smart-todo-digest",
    require_buffer=False,        # agent fetches data itself via list_todos
)
```

---

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LLM_PROVIDER` | yes | auto-detect | `rits` \| `watsonx` \| `openai` \| `anthropic` \| `litellm` \| `ollama` |
| `LLM_MODEL` | no | provider default | Override model name |
| `SMTP_USERNAME` | for email | — | Sender address (shared with newsletter) |
| `SMTP_PASSWORD` | for email | — | SMTP / app password |
| `DIGEST_TO` | for email | — | Recipient address for digest + reminders |
| `DIGEST_SCHEDULE` | no | `0 8 * * 1-5` | Daily digest cron |

---

## Files

| File | Role |
|------|------|
| `app.py` | FastAPI server + UI. Starts `CugaRuntime` (digest) and `CugaWatcher` (reminders) on startup. |
| `agent.py` | `get_agent()` — singleton `CugaAgent`. `make_digest_runtime()` — builds digest pipeline. `make_watcher()` — builds reminder watcher. `process()` — interactive add. |
| `store.py` | SQLite — `save`, `list_all`, `list_due`, `mark_done`. |
| `skills/todo_reasoning.md` | Classification and field extraction rules injected into agent. |
