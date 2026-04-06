# Smart Todo — cuga++ demo

A personal assistant for todos, reminders, and notes. The user talks naturally
in a browser chat. The agent classifies, saves, and retrieves items. Reminders
fire automatically at their due time. A daily digest emails the todo list on a
schedule.

```
python app.py
python app.py --provider anthropic
open http://127.0.0.1:8765
```

---

## What kind of app this is

Three things run concurrently, all wired to the same agent:

| Concern | What triggers it | Who's present |
|---|---|---|
| Interactive chat | User says something | User is present |
| Reminder watcher | `due_date` reached in SQLite | Nobody — autonomous |
| Daily digest | Cron schedule (default: 08:00 weekdays) | Nobody — autonomous |

The user configures everything through conversation — including the digest
schedule and delivery email. There is no settings page, no YAML to edit.

---

## Personas

### Developer

Owns:
- `agent.py` — data tools (`save_todo`, `list_todos`, `mark_done`) + config
  tools (`configure_digest`, `stop_digest`, `get_digest_status`) + reminder
  `CugaWatcher`
- `store.py` — SQLite persistence (no ORM)
- `cuga_pipelines.yaml` — declares the digest pipeline factory for CugaHost
- `skills/todo_reasoning.md` — classification rules, due date resolution,
  confirmation format, digest composition instructions
- `app.py` — wires all four pieces together

### End user

Opens `http://localhost:8765` and talks naturally. The same conversation handles
everything:

```
You: "remind me to send the report to Sarah at 3pm"
Agent: ⏰ Reminder set for 15:00: Send report to Sarah

You: "add a high priority task: review Q2 roadmap before Thursday"
Agent: ✅ Added: Review Q2 roadmap before Thursday [high]

You: "what's still open?"
Agent: 3 active items: (high) Review Q2 roadmap... (medium) ...

You: "mark the roadmap review as done"
Agent: ✅ Marked done: Review Q2 roadmap before Thursday

You: "send my digest to me@company.com at 9am"
Agent: Digest reconfigured — schedule: 0 9 * * *, delivery: me@company.com

You: "stop the digest"
Agent: Digest stopped.
```

At 3pm, without any user action, an email arrives: "⏰ Reminder: Send report to Sarah."

---

## Two agent modes

`make_agent()` is called with or without a `client`:

```python
# In app.py — full agent: data tools + config tools
agent = make_agent(client=client)

# In host_factories.py — digest agent: data tools only (no config needed)
agent = make_agent(client=None)
```

The digest pipeline agent doesn't need to reconfigure itself, so config tools
are omitted. This keeps the digest agent minimal and avoids unnecessary tool
exposure.

---

## How reminders work

`CugaWatcher` polls SQLite every 60 seconds for rows where `todo_type='reminder'`
and `due_date <= now` and `status='active'`. When items are found:

1. Marks each item `done` immediately (prevents double-firing)
2. Invokes the agent to compose a styled HTML reminder body
3. Delivers via `smart_deliver()` — email if SMTP is configured, stdout otherwise
4. Uses `delivery_email` from the item if set (the user can specify per-reminder
   recipients: *"remind bob@co.com about the meeting at 10am"*)

```
[every 60s]
list_due() → []           → silently dropped

[at 15:00]
list_due() → [item #7]
  mark_done(7)
  agent.invoke("Compose reminder: Send report to Sarah")
  → styled HTML
  smart_deliver(html, to="sarah@co.com")   ← per-item recipient
```

---

## How the daily digest works

`CugaHost` manages a `CugaRuntime` with a `CronChannel`. When it fires:

> "Good morning! Compile and send the daily todo digest. Call list_todos to get
> open and done items. Compose a styled HTML email with two sections: Completed
> and Still open, grouped by priority."

The agent calls `list_todos(status='active')` and `list_todos(status='done')`,
composes HTML, and returns it. `CugaRuntime` passes the output to `EmailChannel`.

The user can reconfigure this at any time through chat — the `configure_digest`
tool calls `CugaHostClient.update_runtime()` which tears down the old runtime
and starts a new one with the new schedule.

---

## Architecture

```
Browser (port 8765)
    ↓
ConversationGateway
    ↓
CugaAgent
    ├── save_todo / list_todos / mark_done    → SQLite (todos.db)
    ├── configure_digest / stop_digest        → CugaHostClient → CugaHost
    └── get_digest_status

CugaWatcher (background, every 60s)
    → polls list_due()
    → fires per-item HTML reminders via smart_deliver()

CugaHost (background)
    └── CugaRuntime "smart-todo-digest"
          CronChannel (schedule from config)
          → agent.invoke("Good morning! Send digest...")
          → EmailChannel / LogChannel
```

---

## Configuration

Everything via environment variables or through the chat.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | auto-detect | `rits` \| `anthropic` \| `openai` \| etc. |
| `LLM_MODEL` | provider default | Model name override |
| `DIGEST_SCHEDULE` | `0 8 * * 1-5` | Cron for morning digest (weekdays at 8am) |
| `DIGEST_TO` | none | Default digest email (user can override in chat) |
| `SMTP_USERNAME` | none | SMTP sender address |
| `SMTP_PASSWORD` | none | SMTP password |

If SMTP is not configured, all output goes to stdout via `LogChannel`.

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Entry point — wires agent, watcher, CugaHost, ConversationGateway |
| `agent.py` | Data tools + config tools + `CugaWatcher` reminder factory |
| `store.py` | SQLite CRUD — todos, reminders, notes |
| `cuga_pipelines.yaml` | Digest pipeline declaration for CugaHost |
| `skills/todo_reasoning.md` | Classification rules, due date parsing, digest format |
| `todos.db` | Auto-created SQLite database |
