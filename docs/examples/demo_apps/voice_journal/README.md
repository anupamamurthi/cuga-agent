# Voice Journal — cuga++ demo

A personal journal with a browser chat interface. Type entries, upload voice
notes or documents, and ask questions about past entries. Everything is saved
to dated Markdown files and a local SQLite database.

```
python app.py
python app.py --provider anthropic
open http://127.0.0.1:8766
```

---

## What kind of app this is

Purely interactive. No background pipelines, no scheduled tasks, no CugaHost.
The user opens a browser, talks to the agent, and the agent saves and retrieves
journal entries. The interaction is fully synchronous and user-driven.

This is the simplest cuga++ pattern: one `ConversationGateway`, one agent,
no background work.

---

## Personas

### Developer

Owns:
- `agent.py` — CugaAgent with three tools: `save_journal_entry`, `list_entries`,
  `list_dates`
- `store.py` — SQLite + Markdown persistence (no ORM, no dependencies)
- `skills/journal_reasoning.md` — how to handle voice transcripts vs typed text,
  tone, tagging, formatting rules
- `app.py` — starts `ConversationGateway` with a browser adapter

### End user

Opens the browser and talks naturally:

```
You: [uploads morning-thoughts.m4a]
     "save this as today's entry"

Agent: 📓 Journal entry saved: Morning Thoughts on the Project Deadline

You: "what did I write last week?"
Agent: You have 4 entries from April 1–5. Here's a summary...

You: "show me everything tagged 'work'"
Agent: Found 7 entries tagged work. Most recent: April 5 — "Sprint review..."

You: "how have I been feeling this week?"
Agent: Looking at your entries from the past 7 days, there's a recurring theme
       of energy in the mornings but frustration by late afternoon...
```

---

## File upload — how it works

The browser chat has a paperclip button (📎). The user can attach:

- **Audio** (`.mp3`, `.m4a`, `.wav`, `.ogg`, `.flac`, `.webm`) — transcribed
  with Whisper via `AudioChannel.transcribe()`. Transcript prepended to the
  next chat message.
- **Documents** (`.pdf`, `.txt`, `.md`, `.csv`, any text file) — extracted
  with pypdf or decoded as text.

After transcription/extraction, the content arrives in the chat as:

```
[Transcript of morning-thoughts.m4a]
So I was thinking about the deadline and how we... um... need to probably
push back on the scope. Like, the design isn't... yeah the design isn't ready...
```

The agent's skill file tells it to: clean the transcript, remove filler words,
extract a title, identify tags, format as structured prose, then call
`save_journal_entry`. The user just uploaded a file — the structuring is the
agent's job.

---

## Storage

Each entry is saved in two places simultaneously:

**SQLite** (`journal.db`) — for querying by date, range, tags, recency.

**Markdown** (`journal/YYYY-MM-DD.md`) — one file per day, human-readable,
appendable. Each entry is a `##` section with tags and source label.

```
# Journal — 2026-04-05

## Morning Thoughts on the Project Deadline
*Source: voice*
*Tags: work, planning, deadline*

The project deadline is creating real pressure on the team. The design
work isn't complete and the scope feels too broad for the time available...

---
```

The agent never writes Markdown directly — it calls `save_journal_entry()`,
which delegates to `store.py`. The store handles both formats atomically.

---

## Architecture

```
Browser
    ↓  WebSocket
ConversationGateway
    ↓
CugaAgent
    ├── save_journal_entry  → store.py → SQLite + journal/YYYY-MM-DD.md
    ├── list_entries        → store.py → SQLite query
    └── list_dates          → store.py → SQLite query

File upload (📎):
    Audio → AudioChannel.transcribe() → [Transcript of ...] prepended to message
    PDF   → pypdf extract             → [File: ...] prepended to message
    Text  → decode                    → [File: ...] prepended to message
```

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Entry point — starts ConversationGateway with browser adapter |
| `agent.py` | CugaAgent with three journal tools |
| `store.py` | SQLite + Markdown persistence |
| `skills/journal_reasoning.md` | Agent behaviour: structuring voice transcripts, tone, tagging |
| `journal/` | Auto-created — one `.md` file per day |
| `journal.db` | Auto-created — SQLite database |

---

## Dependencies

```bash
pip install openai-whisper    # local Whisper for audio transcription (no API key)
# or:
# OPENAI_API_KEY is used automatically for cloud Whisper if openai-whisper is not installed

pip install pypdf             # for PDF uploads (optional)
```

Audio transcription falls back gracefully: local Whisper → OpenAI Whisper API →
error message if neither is available.
