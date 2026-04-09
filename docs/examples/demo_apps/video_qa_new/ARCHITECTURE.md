# Video Q&A New — Architecture

## Pattern: Universal Router — Direct Q&A + Folder Watch in One App

One app, one CugaHost registration, two execution modes. The CugaRouter decides per utterance.

---

## Two modes

### DIRECT — on-demand Q&A

User names a video file or asks a question about indexed content. The router sends it straight to the agent.

```
You: "transcribe /recordings/standup.mp4"
      ↓
CugaRouter → DIRECT
      ↓
CugaAgent.invoke()
  └── transcribe_and_index("/recordings/standup.mp4")
        faster-whisper → [{text, start_fmt, end_fmt}] → ChromaDB
  → "Indexed 142 segments, duration 18:34"

You: "what was said about the Q2 budget?"
      ↓
CugaRouter → DIRECT
      ↓
CugaAgent.invoke()
  └── search_video_segments("Q2 budget")
        ChromaDB semantic search → [{text, start_fmt: "10:23", end_fmt: "10:31"}, ...]
  → "[10:23] The Q2 budget was set at $2M, approved by the board..."
```

### PIPELINE — folder watching

User describes what to watch for. The router creates a background runtime.

```
You: "watch /recordings for IBM stock mentions, email me@x.com with timestamps"
      ↓
CugaRouter → PIPELINE
  {data_type: audio, watch_dir: /recordings,
   keywords: [IBM stock], output_type: email, email: me@x.com}
      ↓
CugaRuntime (running forever)
  ├── AudioChannelEnhanced(/recordings)    polls every minute
  │     new file → faster-whisper → [{text, start_fmt, end_fmt}] → buffer
  ├── CronChannel("* * * * *")             fires every minute
  └── CugaAgent (if buffer has items)
        ingest_video_segments → ChromaDB
        search for IBM → finds "[10:23] IBM stock rose 4%..."
        → EmailChannel: "IBM stock mentioned in recording.mp4 at [10:23]..."
```

---

## Shared ChromaDB index

Both modes write to and read from the same ChromaDB collection (`.chroma/video_qa_segments`).

```
Pipeline mode writes:
  AudioChannelEnhanced → agent.ingest_video_segments() → ChromaDB

Direct mode reads:
  agent.search_video_segments("Q2 budget") → ChromaDB → timestamped results
```

Drop a file into `/recordings` → pipeline indexes it automatically → immediately queryable via direct Q&A.

---

## New cuga-channels components

| Component | What it does |
|---|---|
| `AudioChannelEnhanced` | Watches a folder, transcribes with faster-whisper, emits `{segments: [{text, start_fmt, end_fmt}]}` |
| `make_video_tools()` | `transcribe_and_index`, `search_video_segments`, `get_segment_at_time`, `ingest_video_segments` |
| `CugaRouter` | Classifies each utterance: DIRECT, PIPELINE, or CONTROL |

---

## App files

```
video_qa_new/
  agent.py          — make_agent() with make_video_tools()
  skills/
    video_reasoning.md  — system prompt for Q&A + pipeline analysis
  chat.py           — 25 lines: register_app("video-qa") + CugaREPL
  examples.json     — test utterances with expected outputs
  ARCHITECTURE.md   — this file
  .chroma/          — ChromaDB persistence (auto-created)
```

---

## How to run

```bash
# Prerequisites
pip install faster-whisper chromadb cuga cuga-channels cuga-skills
brew install ffmpeg   # for video file support (mp4, mov, mkv)
export ANTHROPIC_API_KEY=...
export SMTP_HOST=smtp.gmail.com   # for email delivery
export SMTP_USER=you@gmail.com
export SMTP_PASS=your-app-password

# Step 1: Start CugaHost (once, keep it running)
cugahost start

# Step 2: Run the app
cd docs/examples/demo_apps/video_qa_new
python chat.py
```

---

## Testing

### Direct Q&A
```
You: transcribe /path/to/meeting.mp4
CUGA: Transcribed and indexed 'meeting.mp4':
      Segments: 142
      Duration: 18:34
      Indexed 142 segment(s) into collection 'video_qa_segments'.

You: what was said about the Q2 budget in meeting.mp4?
CUGA: Results for 'Q2 budget' in 'video_qa_segments':
      [10:23 – 10:31] The Q2 budget was approved at $2M, with 40% allocated to engineering.
      [14:02 – 14:18] The CFO noted the Q2 budget is tight due to hiring freeze.

You: what was discussed at the 10-minute mark in meeting.mp4?
CUGA: [10:00 – 10:12] At this point, the team was reviewing the product roadmap for Q3...
```

### Folder watching pipeline
```
You: watch /recordings for IBM stock mentions, email me@x.com with timestamps
CUGA: Pipeline 'recordings-ibm-watcher' is now running.
      Data:     audio (1 source(s))
      Schedule: * * * * *
      Delivery: me@x.com

# Now drop a video file into /recordings/
# Within 1 minute: AudioChannelEnhanced detects it → transcribes → agent scans
# If IBM mentioned: email arrives with exact timestamp

You: list my pipelines
CUGA: 1 active pipeline(s):
      • recordings-ibm-watcher  (factory=__app__, running=True)
```

### Force immediate trigger (for testing without waiting for new file)
```bash
curl -X POST http://127.0.0.1:18790/runtime/recordings-ibm-watcher/trigger \
  -H "Content-Type: application/json" \
  -d '{"message": "Process buffered videos now."}'
```

### Check ChromaDB index
```bash
curl http://127.0.0.1:18790/runtime/recordings-ibm-watcher/buffer
```
