# Smart Todo — Architecture

## Overview

A natural-language todo manager. The user types free-form text ("remind me to send the Q3 report at noon") and CugaAgent classifies it, extracts structured fields, and persists it. Two background triggers handle autonomous delivery: a scheduled daily digest and a per-reminder email fired when each item becomes due.

---

## The cuga-triggers model: Watch → Emit → React

Every trigger in cuga++ follows this contract:

```
Trigger.start(invoke_fn)
    │
    │  [watches for its event: time tick, HTTP request, DB state, file change, ...]
    │
    │  when event detected:
    │    emit TriggerEvent(source, message, thread_id)
    │    invoke_fn(event)  ──────────────────────────────────────────────────┐
    │                                                                          │
    └──────────────────────────────────────────────────────────────────────   │
                                                                              ▼
                                                              TriggerRuntime._dispatch(event)
                                                                              │
                                                                              ▼
                                                              agent.ainvoke(message, thread_id)
                                                                              │
                                                                              ▼
                                                              LangGraph ReAct loop → tools → LLM
```

**The key design question for each trigger: what belongs in Python vs the LLM?**

| | Python (trigger) | LLM (agent) |
|---|---|---|
| Use when | Determining IF an event occurred; filtering; preventing double-fire | Judgment, composition, tool orchestration |
| Smart todo example (digest) | Time-based: cron fires every weekday 8am — always work to do | Fetch todos, rank by priority, compose HTML email |
| Smart todo example (reminders) | Query DB for due items; mark done before invoking to prevent double-fire | Format and send the reminder email |

Routing the "did an event occur?" check through the LLM is the anti-pattern. Triggers should resolve that in Python and only invoke the agent when there is genuine work for it to do.

---

## Architecture diagram

```
User (browser)
      │
      │  POST /add  {"text": "remind me to ..."}
      ▼
FastAPI  app.py
      │
      │  await process(text)
      ▼
┌─────────────────────────────────────────────────────────────────┐
│                         CugaAgent                                │
│                                                                   │
│  System prompt  ◄── CugaSkillsPlugin(skills/todo_reasoning.md)  │
│                                                                   │
│  LLM ←── _llm.py (RITS / WatsonX / OpenAI / Anthropic / ...)   │
│   │                                                               │
│   ├─► save_todo(content, type, priority, due_date)               │
│   ├─► list_todos(status)                                         │
│   ├─► send_digest_email(subject, html_body)                      │
│   └─► mark_todo_done(todo_id)              ← used by reminders   │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                             SQLite  todos.db
                             (store.py)
```

---

## Triggers

### Trigger 1 — CronTrigger  `"0 8 * * 1-5"`  (daily digest)

```
[Mon–Fri 8:00 AM]
    │
    ▼  APScheduler background thread
CronTrigger._fire()
    │  message: "Good morning! Compile and send the daily todo digest…"
    ▼
agent.ainvoke(message, thread_id="smart-todo-digest")
    │
    ▼  LLM: call list_todos() → rank by priority → compose HTML → send_digest_email()
```

A generic time-based event. The cron always fires regardless of DB state — the LLM is responsible for fetching and composing. `CronTrigger` is the right fit here.

---

### Trigger 2 — ReminderTrigger  (custom, every 60 s)

```
[every 60 seconds]
    │
    ▼  APScheduler background thread
ReminderTrigger._check()
    │
    ├─► list_due()  ←── Python DB query: WHERE todo_type='reminder' AND due_date ≤ now
    │
    │   if empty: return immediately — agent is never invoked
    │
    └─► for each due item:
          mark_done(item.id)                    ← Python: prevents double-fire before agent runs
          emit TriggerEvent(
              message="Send reminder email for: '{content}'…",
              thread_id=f"reminder-{id}"
          )
          agent.ainvoke(message, thread_id)
              │
              └─► LLM: call send_digest_email(subject, html_body) → SMTP
```

`ReminderTrigger` is a **custom trigger** that satisfies the `CugaTrigger` protocol (duck-typed — just needs `name`, `start(invoke_fn)`, `stop()`). It implements the watch in Python because:

- Only invokes the agent when items are actually due (no wasted LLM calls)
- Marks reminders done *before* invoking the agent — a failed or slow LLM call cannot cause double-fire
- Passes the exact reminder content in the message — the LLM only needs to call `send_digest_email` once

---

## cuga++ package roles

| Package | Role in this app |
|---------|-----------------|
| `cuga` (CugaAgent) | Runs the LangGraph ReAct loop. Accepts `triggers=[]`, starts `TriggerRuntime` internally. Built-in checkpointing keeps multi-turn context per `thread_id`. |
| `cuga-skills` | `CugaSkillsPlugin` injects `skills/todo_reasoning.md` into every system prompt — classification rules, field extraction, priority logic. |
| `cuga-triggers` | `CronTrigger` (daily digest). `ReminderTrigger` implements `CugaTrigger` protocol — uses APScheduler internally, same as `CronTrigger`. |
| `cuga-runtime` | `RITSChatModel` in `_llm.py` — sends auth as `RITS_API_KEY` header (custom IBM RITS requirement, not standard Bearer). |

---

## Files

| File | Role |
|------|------|
| `app.py` | FastAPI server + inline UI. Calls `get_agent()` on startup (starts both triggers). |
| `agent.py` | Singleton `CugaAgent`. Defines all tools. Defines `ReminderTrigger`. |
| `store.py` | SQLite wrapper — `save`, `list_all`, `list_due`, `mark_done`. |
| `skills/todo_reasoning.md` | Classification and field extraction rules injected into every system prompt. |
| `_llm.py` | Multi-provider LLM factory (shared across all demo apps). |

---

## Data flow — saving a reminder

```
1. User: "remind me to review the PR at 3pm"
   └─► POST /add → process("remind me to review the PR at 3pm")

2. CugaAgent (with todo_reasoning.md skill):
   LLM infers → todo_type="reminder", due_date="...T15:00:00", priority="medium"
   └─► Tool: save_todo(content="Review the PR", type="reminder", due_date="...T15:00:00")
       └─► SQLite row inserted, status='active'

3. Response: {"todo": {...}, "reasoning": "Saved reminder: Review the PR at 3:00 PM."}
```

## Data flow — reminder fires

```
4. [next 60s tick]  ReminderTrigger._check()
   └─► list_due() → [{id:1, content:"Review the PR", due_date:"...T15:00:00"}]
   └─► mark_done(1) → status='done' in SQLite          ← happens before LLM
   └─► agent.ainvoke("Send reminder email for: 'Review the PR'…", thread_id="reminder-1")
       └─► Tool: send_digest_email("⏰ Reminder: Review the PR", <HTML>) → SMTP
```

---

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LLM_PROVIDER` | yes | auto-detect | `rits` \| `watsonx` \| `openai` \| `anthropic` \| `litellm` \| `ollama` |
| `LLM_MODEL` | no | provider default | Override model name |
| `RITS_API_KEY` | if rits | — | IBM RITS auth |
| `SMTP_USER` | for email | — | Sender address |
| `SMTP_PASSWORD` | for email | — | SMTP/app password |
| `DIGEST_TO` | for email | `SMTP_USER` | Recipient(s) |
| `DIGEST_SCHEDULE` | no | `0 8 * * 1-5` | Daily digest cron |
