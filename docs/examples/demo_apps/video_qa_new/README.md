# Video Q&A (New)

One app, two modes — ask questions about a specific video on demand, or set up
a background watcher that monitors a folder and alerts you when keywords are
mentioned in new recordings.

Powered by CugaHost for routing and pipeline management.

---

## Division of Responsibilities

### The Infrastructure (CugaHost + channels)

- **CugaRouter** — classifies each utterance as `DIRECT`, `PIPELINE`, or `CONTROL`
- **AudioChannelEnhanced** — watches a folder, detects new audio/video files, runs
  faster-whisper locally, emits transcript segments — **no LLM involved**
- **CronChannel** — fires the agent on a schedule to process buffered segments
- **EmailChannel** — delivers reports — **no LLM involved**
- **CugaHost** — manages all running pipelines, restores them across restarts

The infrastructure decides *when* to run and *what data* to collect.
It invokes the agent only for analysis and Q&A.

### CugaAgent

The agent receives transcript segments (already extracted) and either answers a
question or scans for keyword mentions and composes a report.

| Mode | Input | Output |
|---|---|---|
| DIRECT — transcribe | File path | Confirmation + segment count |
| DIRECT — Q&A | Question + indexed video name | Timestamped answer |
| PIPELINE — new segments buffered | Transcript segments | Keyword report with timestamps |

### Agent Tools

Provided by `cuga_channels.make_video_tools()`:

| Tool | What it does |
|---|---|
| `transcribe_and_index(path)` | Run faster-whisper on a file, store segments in ChromaDB |
| `ingest_video_segments(segments)` | Store pre-transcribed segments from `AudioChannelEnhanced` |
| `search_video_segments(query)` | Semantic search in ChromaDB → segments with timestamps |
| `get_segment_at_time(seconds)` | Return segment covering a given timestamp |

Transcription in PIPELINE mode is done by `AudioChannelEnhanced` (app layer)
before the agent is called. The agent calls `ingest_video_segments` to store them,
then `search_video_segments` to find keyword matches.

### Skills

| Skill | Purpose |
|---|---|
| `skills/video_reasoning.md` | Tool usage order, timestamp format, keyword scanning, report format |

---

## Quick Start

```bash
# 1. Start CugaHost (once — keep it running)
cugahost start

# 2. Run the chat client
cd docs/examples/demo_apps/video_qa_new
python chat.py
```

---

## Example Utterances

```
# Direct Q&A
"transcribe /path/to/meeting.mp4"
"what was said about the Q2 budget in meeting.mp4?"
"what was discussed at the 10-minute mark in standup.mp4?"

# Folder watching pipeline
"watch /recordings for IBM stock mentions, email me@x.com with timestamps"
"monitor /meetings — if AI strategy is discussed, alert cto@company.com"
"every morning summarise new videos from /uploads, email team@company.com"

# Management
"list my pipelines"
"stop the recordings watcher"
```

---

## How the Two Modes Work

### DIRECT — on-demand Q&A

```
You: "what was said about Q2 budget in meeting.mp4?"
       │
       ▼
CugaRouter → DIRECT → agent.invoke()
       │
       ▼
agent calls search_video_segments("Q2 budget")
       → ChromaDB semantic search
       → [{text, start_fmt: "10:23", end_fmt: "10:31"}, ...]
       │
       ▼
"[10:23] The Q2 budget was approved at $2M..."
```

### PIPELINE — folder watching

```
You: "watch /recordings for IBM mentions, email me@x.com"
       │
       ▼
CugaRouter → PIPELINE → CugaHost builds runtime:
  AudioChannelEnhanced(/recordings)   polls every minute
      new file → faster-whisper → [{text, start, end}]  (no LLM)
      → buffer segments
  CronChannel("* * * * *")            fires every minute
      → agent.invoke(buffered segments)
          → ingest_video_segments()     store in ChromaDB
          → search_video_segments("IBM stock")
          → "IBM stock mentioned in recording.mp4 at [10:23]..."
  EmailChannel(to="me@x.com")         delivers report
```

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `ollama` |
| `LLM_MODEL` | Model name override |
| `SMTP_USERNAME` | SMTP sender address |
| `SMTP_PASSWORD` | SMTP app password |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `CUGAHOST_PORT` | CugaHost port (default: `18790`) |

---

## Files

| File | Purpose |
|---|---|
| `chat.py` | CLI client — registers app with CugaHost, starts REPL |
| `agent.py` | `make_agent()` — CugaAgent with `make_video_tools()` |
| `skills/video_reasoning.md` | Agent skill — Q&A format, keyword scanning, report format |
| `requirements.txt` | Python dependencies |
| `.chroma/` | ChromaDB vectors (auto-created) |

See [ARCHITECTURE.md](ARCHITECTURE.md) for component diagrams and data flows.
