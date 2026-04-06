# Video Q&A — cuga++ demo

Transcribe a video or audio recording, then ask questions about it in natural
language and get answers with exact timestamps.

```
python run.py meeting.mp4                              # interactive CLI
python run.py meeting.mp4 --ask "where was M3 discussed?"  # single question
python run.py --web                                    # browser UI at localhost:8766
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full technical deep-dive.

---

## What kind of app this is

This is a **direct Q&A app**, not a pipeline. There are no background channels,
no scheduled triggers, no CugaHost. The user loads a video and asks questions.
The interaction is synchronous and user-driven — the agent only runs when the
user explicitly asks something.

Contrast with the newsletter demo, which runs autonomously on a cron schedule
without the user being present. Here, the user is always in the loop.

---

## Personas

### Developer

Configures and ships the app. Owns:
- `agent.py` — `VideoQAAgent` class, three LangChain tools, CugaAgent setup
- `transcriber.py` — Whisper pipeline, ffmpeg audio extraction, disk caching
- `index.py` — ChromaDB vector index, semantic search, timestamp lookup
- `skills/video_qa.md` — how the agent should use its tools, timestamp format,
  citation rules, "not found" behaviour
- `run.py` — CLI + web UI (FastAPI + inline HTML, no external frontend)

No config files for the developer to ship. No YAML. The app is fully
self-contained.

### End user

Runs the app and interacts with it. No setup beyond dependencies.

```
python run.py meeting.mp4

  Transcribing… (cached on disk after first run)
  Done — 142 segments, duration 47:12

  Ask anything about the video. Type 'exit' to quit.

  You: Where was M3 discussed?
  Agent: M3 was introduced at 00:04 and benchmarks were covered at 10:02 – 11:45.

  You: What decisions were made?
  Agent: Three decisions were recorded...

  You: What was said around the 30-minute mark?
  Agent: At 29:58 the speaker said...
```

Or in the browser:

```
python run.py --web
→  http://127.0.0.1:8766
```

The browser UI adds a transcript panel — all segments with timestamps, filterable
by keyword. Clicking a segment pre-fills the question box.

---

## Two phases — only one uses the LLM

**Phase 1 — Transcription** (Python only, no LLM)

```
meeting.mp4
    → ffmpeg extracts audio
    → faster-whisper produces [{start, end, text}, ...]
    → sentence-transformers embeds each segment
    → ChromaDB stores vectors on disk
```

This runs once per file and is cached. Re-running the app on the same file skips
it entirely. The LLM has no role here — transcription is deterministic and
mechanical, so it stays in Python.

**Phase 2 — Answering** (CugaAgent)

```
User question
    → CugaAgent (guided by skills/video_qa.md)
    → calls search_transcript(query)    — ChromaDB cosine similarity
    → calls get_segment_at_time(s)      — timestamp lookup
    → composes answer with bold timestamps
```

Only retrieval and reasoning go through the LLM. The skill file tells the agent
exactly when to use each tool, how to format timestamps, and what to say when
nothing is found.

---

## Why no channels or triggers

Channels and triggers exist for **autonomous, event-driven work** — things that
should happen on a schedule or in response to an event, without a user present.

Video Q&A is the opposite. A user sits at a terminal or browser, asks a
question, and expects an immediate answer. There is no "run while I'm away"
requirement. Adding a `CronChannel` or `CugaHost` here would be pure overhead.

Triggers *would* apply if you extended this:

| Extension | What to add |
|---|---|
| Auto-transcribe recordings dropped in a folder | `DoclingChannel`-style `DataChannel` |
| Nightly summary of all meetings transcribed that day | `CronChannel` + `CugaRuntime` |
| Transcribe on webhook POST | `WebhookChannel` |

---

## Files

| File | Purpose |
|---|---|
| `run.py` | Entry point — CLI REPL and web UI (FastAPI + inline HTML) |
| `agent.py` | `VideoQAAgent` — wraps CugaAgent, owns transcription + ask |
| `transcriber.py` | Whisper pipeline, ffmpeg extraction, segment caching |
| `index.py` | ChromaDB vector index — semantic search and timestamp lookup |
| `skills/video_qa.md` | Agent instructions: tool usage, timestamp format, citation rules |
| `ARCHITECTURE.md` | Full technical architecture with data flow diagrams |

---

## Dependencies

```bash
pip install faster-whisper chromadb sentence-transformers fastapi uvicorn
brew install ffmpeg       # for video files (.mp4, .mov, .mkv, ...)
```

Audio-only files (`.wav`, `.mp3`, `.m4a`) skip ffmpeg entirely.

Transcripts and vector indexes are cached in `.cache/` — delete this folder to
force a full re-transcription.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `ollama` \| `litellm` |
| `LLM_MODEL` | Model name override (optional) |
