# Video Q&A — Architecture

## Overview

Upload a video or audio recording; ask questions about it in natural language and get answers with precise timestamps. CugaAgent orchestrates a Whisper transcription pipeline, a ChromaDB semantic index, and a point-in-time segment lookup to compose answers that cite exact timestamps.

No triggers are used — this is a purely interactive, request-response application. If you wanted to extend it (e.g. "watch a folder for new recordings and auto-transcribe"), a custom `CugaTrigger` would be the right hook — see the extension note below.

---

## The cuga-triggers model (not used here, but relevant for extension)

Every trigger in cuga++ follows this contract:

```
Trigger.start(invoke_fn)
    │
    │  [watches for its event: time tick, HTTP request, DB state, file change, ...]
    │
    │  when event detected:
    │    emit TriggerEvent(source, message, thread_id)
    │    invoke_fn(event)
    │         │
    │         ▼
    │    TriggerRuntime._dispatch(event)
    │         │
    │         ▼
    │    agent.ainvoke(message, thread_id)
    │         │
    │         ▼
    │    LangGraph ReAct loop → tools → LLM → answer
    │
    │  trigger's responsibility: detect the event, emit a clear message
    │  agent's responsibility:   decide what to do and do it
```

For video Q&A specifically, the "event" is a user question — synchronous and user-driven — so no background trigger is needed. The user calls `agent.ask()` directly.

---

## Architecture diagram

```
              ┌──────────────────────────────────┐
              │        VideoQAAgent wrapper        │
              │           (agent.py)              │
              └────────────────┬─────────────────┘
                               │
         ┌─────────────────────┴───────────────────────────┐
         │                                                   │
         │  transcribe(video_path)                          │
         │  called directly, bypasses LLM                   │
         │                                                   │
         │  transcriber.py                                  │
         │    ffmpeg → extract audio (if video file)        │
         │    faster-whisper → [{start, end, text}, ...]    │
         │    cached on disk — never re-transcribed         │
         │                                                   │
         │  index.py                                        │
         │    sentence-transformers → embed each segment    │
         │    ChromaDB → upsert vectors (keyed by path)     │
         └───────────────────────────────────────────────────┘

         ┌───────────────────────────────────────────────────┐
         │  ask(question)                                     │
         │  goes through CugaAgent                           │
         │                                                   │
         │  ┌─────────────────────────────────────────────┐  │
         │  │              CugaAgent                       │  │
         │  │                                               │  │
         │  │  System prompt  ◄── CugaSkillsPlugin(        │  │
         │  │                      skills/video_qa.md)     │  │
         │  │                   tool usage rules,          │  │
         │  │                   timestamp format,          │  │
         │  │                   "not found" handling       │  │
         │  │                                               │  │
         │  │  LLM ←── _llm.py                             │  │
         │  │   │                                           │  │
         │  │   ├─► search_transcript(query, n_results)    │  │
         │  │   │     ChromaDB cosine similarity           │  │
         │  │   │     → [{text, start_fmt, end_fmt,        │  │
         │  │   │          distance}]                      │  │
         │  │   │                                           │  │
         │  │   └─► get_segment_at_time(seconds)           │  │
         │  │         binary search in segments array      │  │
         │  │         → {text, start_fmt, end_fmt}         │  │
         │  │                                               │  │
         │  │  LLM composes answer with citations          │  │
         │  └─────────────────────────────────────────────┘  │
         └───────────────────────────────────────────────────┘
```

---

## Why no triggers here

Triggers are for **autonomous, event-driven execution** — something that should happen without a user explicitly asking. Video Q&A is the opposite: a user sits at a CLI or browser, asks a question, and expects an answer immediately. The interaction is synchronous and user-initiated.

Triggers *would* apply if you extended this to:

| Extension | Trigger type |
|-----------|-------------|
| Auto-transcribe recordings dropped in a folder | Custom `FolderWatchTrigger` (uses `watchdog` or polling) |
| Nightly summary of all meetings transcribed that day | `CronTrigger` |
| "Transcribe this recording" via a webhook | `WebhookTrigger` |

In each case the trigger would:
1. **Watch** — detect the event in Python (new file, time tick, HTTP POST)
2. **Emit** — `TriggerEvent(message="Transcribe and summarise /path/to/meeting.mp4", thread_id=...)`
3. **React** — CugaAgent calls `transcribe_video` then `search_transcript` for a summary

The `CugaTrigger` protocol is duck-typed (just `name`, `start(invoke_fn)`, `stop()`), so any of these can be added without changing the agent.

---

## Transcription pipeline (outside the agent)

`transcribe()` is called directly on `VideoQAAgent`, not through the LLM. This is deliberate:

- Transcription is **deterministic and mechanical** — Whisper produces a fixed output for a given file
- It's **long-running** — the LLM has no role in driving it; it would just be waiting
- It's **cached** — the same file is never re-processed

Only the *retrieval and answering* step — where semantic judgment is needed — goes through CugaAgent.

```
transcribe(video_path)
    │  [Python only — no LLM]
    ├─► transcriber.py: ffmpeg + faster-whisper → segments
    └─► index.py: embed + ChromaDB upsert

ask(question)
    │  [CugaAgent]
    └─► LLM → search_transcript / get_segment_at_time → compose answer with timestamps
```

This mirrors the principle in `smart_todo`'s `ReminderTrigger`: **use Python for the deterministic part, use the LLM only where judgment is needed**.

---

## cuga++ package roles

| Package | Role in this app |
|---------|-----------------|
| `cuga` (CugaAgent) | Runs the LangGraph ReAct loop for question answering. Built-in checkpointing makes multi-turn work: "summarise that section" can refer back to the previous answer in the same thread. |
| `cuga-skills` | `CugaSkillsPlugin` injects `skills/video_qa.md` — tool usage order, timestamp format, citation rules, "no answer found" handling. |
| `cuga-triggers` | Not used. No background or scheduled tasks. |
| `cuga-runtime` | `RITSChatModel` in `_llm.py` — custom IBM RITS auth header. |

---

## Files

| File | Role |
|------|------|
| `agent.py` | `VideoQAAgent` class — lazy-builds CugaAgent, exposes `transcribe()` and `ask()`. |
| `transcriber.py` | Runs faster-whisper (optionally via ffmpeg). Returns `[{start, end, text}]`. Caches on disk. |
| `index.py` | Embeds with sentence-transformers, stores in ChromaDB. Provides `search(query)` and `get_at_time(seconds)`. |
| `skills/video_qa.md` | Tool usage rules and answer formatting injected into every system prompt. |
| `run.py` | CLI (`python run.py meeting.mp4`) and web UI (`--web`, FastAPI + inline HTML). |
| `_llm.py` | Multi-provider LLM factory (shared across all demo apps). |

---

## Data flow

### Transcription (one-time)

```
meeting.mp4
    │
    ▼  ffmpeg (video → audio)
    │
    ▼  faster-whisper
    [{start:0.0, end:4.2, text:"Good morning everyone…"},
     {start:4.2, end:9.1, text:"Today we'll cover the M3 roadmap…"},
     …]
    │
    ├─► held in memory (video_path_ref["segments"])
    └─► sentence-transformers embeds → ChromaDB (persisted on disk)
```

### Answering a question

```
User: "Where was M3 discussed?"
    │
    ▼  agent.ask(question) → CugaAgent.invoke(question, thread_id="video-qa")
    │
    │  LLM (guided by video_qa.md skill):
    │    "call search_transcript with a focused query"
    ▼
search_transcript("M3 roadmap")
    │
    ▼  ChromaDB cosine similarity
    [{text:"M3 roadmap…", start_fmt:"00:04", distance:0.12},
     {text:"M3 benchmarks…", start_fmt:"10:02", distance:0.18}]
    │
    ▼  LLM composes:
    "M3 was introduced at **00:04** and benchmarks were covered at **10:02 – 11:45**."
```

---

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LLM_PROVIDER` | yes | auto-detect | `rits` \| `watsonx` \| `openai` \| etc. |
| `LLM_MODEL` | no | provider default | Override model name |
| `RITS_API_KEY` | if rits | — | IBM RITS auth |

External dependencies (install once):
```bash
pip install faster-whisper chromadb sentence-transformers
brew install ffmpeg   # for video files
```
