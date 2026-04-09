# Video Q&A New — Architecture

## Design principle

**Transcription and indexing are app-layer concerns. The agent handles
retrieval and reasoning only.**

`AudioChannelEnhanced` transcribes files locally (faster-whisper, no LLM) before
the agent is ever called. The agent receives pre-extracted segments and decides
what to surface. This keeps the agent fast and cheap — it is not doing OCR,
it is doing judgment.

---

## Component map

```
┌─────────────────────────────────────────────────────────────────┐
│  Infrastructure (CugaHost daemon)                               │
│                                                                 │
│  PIPELINE mode:                                                 │
│  ┌──────────────────────────┐                                   │
│  │ AudioChannelEnhanced     │  watches folder, no LLM          │
│  │ /recordings              │                                   │
│  │  new file detected       │                                   │
│  │  → ffmpeg extract audio  │                                   │
│  │  → faster-whisper        │                                   │
│  │  → [{text, start, end}]  │                                   │
│  │  → buffer                │                                   │
│  └──────────┬───────────────┘                                   │
│             │                                                   │
│  ┌──────────▼───────────────┐                                   │
│  │ CronChannel              │  fires on schedule               │
│  └──────────┬───────────────┘                                   │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────┐               │
│  │  CugaAgent                                   │               │
│  │                                              │               │
│  │  tools (from make_video_tools()):            │               │
│  │    transcribe_and_index(path)               │               │
│  │    ingest_video_segments(segments)          │               │
│  │    search_video_segments(query)             │               │
│  │    get_segment_at_time(seconds)             │               │
│  │                                              │               │
│  │  skill: video_reasoning.md                  │               │
│  │                                              │               │
│  │  → keyword report with timestamps           │               │
│  └──────────────┬───────────────────────────────┘               │
│                 │                                               │
│                 ▼                                               │
│  ┌──────────────────────────┐                                   │
│  │ EmailChannel             │  delivers report, no LLM         │
│  └──────────────────────────┘                                   │
│                                                                 │
│  DIRECT mode:                                                   │
│  ┌──────────────────────────┐                                   │
│  │ CugaRouter               │  LLM classifier in CugaHost      │
│  │ mode=DIRECT              │                                   │
│  └──────────┬───────────────┘                                   │
│             │                                                   │
│             ▼                                                   │
│  CugaAgent.invoke(question)                                     │
│    → search_video_segments(query) → ChromaDB                   │
│    → timestamped answer                                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  App layer (chat.py)                                            │
│  register_app("video-qa", agent="agent:make_agent", ...)       │
│  CugaREPL loop → POST /app/video-qa/chat → CugaRouter          │
└─────────────────────────────────────────────────────────────────┘
```

---

## What the infrastructure owns

| Responsibility | Component | LLM? |
|---|---|---|
| Folder watching | `AudioChannelEnhanced` | No |
| Audio extraction | ffmpeg inside `AudioChannelEnhanced` | No |
| Transcription | faster-whisper inside `AudioChannelEnhanced` | No |
| Schedule management | `CronChannel` | No |
| Email delivery | `EmailChannel` | No |
| Utterance routing | `CugaRouter` (direct LLM call, not CugaAgent) | Yes (classifier only) |
| Pipeline lifecycle | `CugaHost` daemon | No |

## What CugaAgent owns

| Responsibility | How | LLM? |
|---|---|---|
| Index pre-transcribed segments | `ingest_video_segments` tool | Yes |
| Keyword search with context | `search_video_segments` tool | Yes |
| Compose keyword reports | Agent reasoning | Yes |
| Direct Q&A | `search_video_segments` + `get_segment_at_time` | Yes |
| On-demand transcription (DIRECT) | `transcribe_and_index` tool | Calls Whisper — no LLM for transcription itself |

---

## Agent configuration

```python
# agent.py
from cuga_channels import make_video_tools

CugaAgent(
    model   = create_llm(...),
    tools   = make_video_tools(
        collection_name = "video_qa_segments",
        persist_dir     = ".chroma",
    ),
    plugins = [CugaSkillsPlugin(...)],   # video_reasoning.md skill
)
```

## Shared ChromaDB index

Both modes write to and read from the same collection (`.chroma/video_qa_segments`).

```
PIPELINE: AudioChannelEnhanced → agent.ingest_video_segments() → ChromaDB
DIRECT:   agent.transcribe_and_index(path)                     → ChromaDB

Both:     agent.search_video_segments(query)                  ← ChromaDB
```

A file dropped into the watched folder is automatically indexed and immediately
queryable via DIRECT mode.

---

## PIPELINE data flow

```
1.  User: "watch /recordings for IBM mentions, email me@x.com"
2.  CugaRouter → PIPELINE config extracted
3.  CugaHost builds runtime:
      AudioChannelEnhanced("/recordings") — polls every minute
      CronChannel("* * * * *")
      CugaAgent + video_reasoning.md skill
      EmailChannel(to="me@x.com")

Every minute:
4.  AudioChannelEnhanced checks /recordings for new files
5.  New file: ffmpeg → faster-whisper → [{text, start, end}] → buffer

CronChannel fires:
6.  agent.invoke(buffered_segments, thread_id="pipeline-video-qa")
      → agent calls ingest_video_segments(segments)
            → ChromaDB stores all segments
      → agent calls search_video_segments("IBM")
            → ChromaDB cosine search → [{text: "IBM stock rose 4%", start_fmt: "10:23", ...}]
      → agent composes: "IBM stock mentioned in recording.mp4 at [10:23]: ..."

7.  EmailChannel sends report to me@x.com
```

## DIRECT data flow

```
1.  User: "what was said about Q2 budget in meeting.mp4?"
2.  CugaRouter → DIRECT → agent.invoke()
3.  agent calls search_video_segments("Q2 budget")
      → ChromaDB → top matching segments
4.  agent: "[10:23] The Q2 budget was approved at $2M..."
```

---

## Three routing modes

**DIRECT** — answer immediately, agent invoked once:
```
"what was said about X in Y.mp4?" → search + answer
"transcribe /path/to/file.mp4"    → transcribe_and_index
```

**PIPELINE** — infrastructure set up, runs in background:
```
"watch /recordings for IBM, email me" → AudioChannel + CronChannel + EmailChannel
```

**CONTROL** — manage pipelines:
```
"list my pipelines"               → active pipeline list
"stop the recordings watcher"     → runtime stopped
```
