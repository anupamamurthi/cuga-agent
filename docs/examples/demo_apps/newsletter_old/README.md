# Newsletter — cuga++ demo

An AI-powered newsletter that monitors RSS feeds **and podcast audio**, curates
content with Whisper transcription, and delivers a styled HTML digest on a schedule.

Demonstrates the full multimodal pipeline pattern:

```
DataChannel (RSS)      ─┐
DataChannel (Podcast)  ─┼─► ChannelBuffer ─► TriggerChannel ─► CugaAgent ─► OutputChannel
  └─ Whisper transcribe ┘                      (CronChannel)                 (EmailChannel)
```

---

## Quick start

```bash
# Terminal entry point (interactive REPL)
python chat.py

# Or pass a one-liner directly
python chat.py "watch arxiv cs.AI and Practical AI podcast for agent research, email me@example.com every 4 hours"

# Browser chat at localhost:8769
python app.py
```

### Provider flags

```bash
python chat.py --provider anthropic
python chat.py --provider openai --model gpt-4o
python chat.py --provider rits
```

### Example utterances

```
"monitor arxiv and the Practical AI podcast for AI agents work, email me every 4 hours"
"watch Latent Space podcast and huggingface for LLM research, digest daily at 9am"
"fetch the latest right now"
"status"   /   "stop"
"add the TWIML podcast to my sources"
"only include podcast episodes under 60 minutes"
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  chat.py / app.py  (front door — minimal, ~10 lines of app logic)       │
│                                                                          │
│  User NL utterance                                                       │
│    └─► ChannelPlanner (planning agent — runs ONCE at setup)             │
│          └─► configure_monitor(sources, podcast_sources, keywords, ...) │
│                └─► CugaHostClient.start_runtime("newsletter", config)   │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ HTTP / embedded
┌──────────────────────────────────▼──────────────────────────────────────┐
│  CugaHost  (cuga++ daemon — knows nothing about newsletters)             │
│  Owns CugaRuntime instances. Persists configs → restores on restart.    │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ builds + runs
┌──────────────────────────────────▼──────────────────────────────────────┐
│  CugaRuntime                                                             │
│                                                                          │
│  INPUT CHANNELS                              TRIGGER                     │
│  ┌─────────────────────────┐                ┌──────────────────────┐    │
│  │ RssChannel              │                │ CronChannel          │    │
│  │  polls arxiv, HF every  │                │  fires every 4 hours │    │
│  │  15 min, keyword filter │─► buffer       │  (or user-specified) │    │
│  │  pushes text items      │    ▲           └──────────┬───────────┘    │
│  └─────────────────────────┘    │                      │                │
│                                 │                      ▼                │
│  ┌─────────────────────────┐    │           buffer has items?           │
│  │ PodcastChannel          │    │             yes → invoke agent        │
│  │  polls podcast RSS      │    │                                       │
│  │  every 60 min           │    │           CUGA AGENT                  │
│  │  keyword filter on meta │    │           ┌──────────────────────┐    │
│  │  if hit:                │    │           │ reads buffer items   │    │
│  │    download audio ──────┼────┘           │ (RSS + podcast)      │    │
│  │    Whisper transcribe   │                │ applies              │    │
│  │    push transcript item │                │ newsletter_curation  │    │
│  └─────────────────────────┘                │ skill                │    │
│                                             │ produces HTML digest │    │
│                                             └──────────┬───────────┘    │
│  OUTPUT CHANNEL                                        │                │
│  ┌─────────────────────────────────────────────────────▼───────────┐   │
│  │ EmailChannel  →  delivers HTML to recipient inbox               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## End-to-end walkthrough

**User says:**
```
"watch arxiv cs.AI and the Practical AI podcast for AI agents, email me@example.com every 4 hours"
```

### Step 1 — ChannelPlanner (cuga, planning agent, runs once)

Parses the NL into a structured config dict:
```python
{
  "intent":          "monitor",
  "sources":         ["https://arxiv.org/rss/cs.AI"],          # user-specified
  "podcast_sources": ["https://changelog.com/practicalai/feed"], # user-specified
  "keywords":        ["AI agents", "agent", "agentic"],         # extracted
  "digest_minutes":  240,                                        # "every 4 hours"
  "email":           "me@example.com",
}
```

**In:** raw utterance string
**Out:** structured config dict

---

### Step 2 — CugaHost builds CugaRuntime

`CugaHostClient.start_runtime("newsletter-monitor", "newsletter", config)` →
CugaHost looks up the `"newsletter"` factory (registered from `cuga_pipelines.yaml`) →
builds a live `CugaRuntime` with:

| Component | Instance |
|---|---|
| DataChannel | `RssChannel(sources=[arxiv cs.AI], keywords=["agent",...], poll_minutes=15)` |
| DataChannel | `PodcastChannel(sources=[Practical AI], keywords=["agent",...], poll_minutes=60)` |
| TriggerChannel | `CronChannel(schedule="0 */4 * * *", message="time to send digest")` |
| Agent | `CugaAgent` with `newsletter_curation` skill |
| OutputChannel | `EmailChannel(to="me@example.com")` + `LogChannel()` |

---

### Step 3 — RssChannel (every 15 min)

Polls `https://arxiv.org/rss/cs.AI`. Finds 3 papers matching "agent":

```python
# Pushed to ChannelBuffer:
[
  {"title": "AgentBench: Evaluating LLMs as Agents", "url": "...", "summary": "...", "source": "arxiv.org"},
  {"title": "ReAct: Synergizing Reasoning and Acting",  "url": "...", "summary": "...", "source": "arxiv.org"},
  {"title": "Toolformer: Language Models Can Teach Themselves", "url": "...", "summary": "...", "source": "arxiv.org"},
]
```

**In:** HTTP RSS XML from arxiv
**Out:** 3 dicts → `ChannelBuffer.add()`  (deduped by URL)

---

### Step 4 — PodcastChannel (every 60 min)  ← multimodal step

1. Fetches `https://changelog.com/practicalai/feed` (XML, no audio yet)
2. Finds episode: **"AI Agents in Production"** — title matches keyword "agent" ✓
3. `<itunes:duration>45:12</itunes:duration>` → 45 min ≤ 90 min limit ✓
4. Downloads the `.mp3` (~42 MB) to memory
5. Calls `AudioChannel.transcribe(audio_bytes, "episode.mp3", model="tiny")` → Whisper runs
6. Pushes to buffer:

```python
{
  "title":              "AI Agents in Production",
  "url":                "https://changelog.com/practicalai/...",
  "audio_url":          "https://cdn.changelog.com/uploads/.../episode.mp3",
  "summary":            "This week we discuss deploying autonomous AI agents...",
  "transcript_excerpt": "...we've seen a big shift toward tool-calling agents
                          that can browse the web, write code, and check databases
                          without human intervention. The key challenge isn't the
                          model — it's the scaffolding...",
  "source":             "podcast: Practical AI",
  "published":          "Mon, 07 Apr 2026 10:00:00 +0000",
  "duration_minutes":   45,
}
```

**In:** podcast RSS XML → keyword match → `.mp3` bytes → Whisper
**Out:** 1 dict with `transcript_excerpt` → `ChannelBuffer.add()`

---

### Step 5 — CronChannel fires (every 4 hours)

`CronChannel` fires with message: `"It's time to send the newsletter digest."`

`CugaRuntime._on_trigger()`:
- Reads buffer: 3 RSS items + 1 podcast item = 4 items total
- Serialises them as JSON into the trigger message
- Calls `CugaAgent.invoke(message + items, thread_id="newsletter-digest")`

**In:** trigger message + 4 buffered item dicts
**Out:** agent invocation

---

### Step 6 — CugaAgent (cuga, newsletter writer)

Receives the combined message. Applies the `newsletter_curation` skill.

- Groups arxiv papers → **Research Papers** section
- Groups podcast item → **Podcast Highlights** section, extracts key quote from `transcript_excerpt`
- Renders full HTML newsletter

```html
<!-- Research Papers section -->
<h2>Research Papers</h2>
<div>
  <a href="...">AgentBench: Evaluating LLMs as Agents</a>
  <p>A benchmark for evaluating LLMs as autonomous agents across 8 environments...</p>
  <span>arxiv.org · Apr 7</span>
</div>
...

<!-- Podcast Highlights section -->
<h2>🎙️ Podcast Highlights</h2>
<div style="border-left:3px solid #2563eb; ...">
  <a href="...">AI Agents in Production — Practical AI</a>
  <p>Deploying autonomous agents: the scaffolding challenges beyond the model.</p>
  <blockquote>
    "The key challenge isn't the model — it's the scaffolding..."
  </blockquote>
  <span>podcast: Practical AI · 45 min · Apr 7</span>
</div>
```

**In:** 4 item dicts (RSS + podcast)
**Out:** complete HTML string

---

### Step 7 — EmailChannel delivers

`CugaRuntime` passes `result.answer` (the HTML) to `EmailChannel.deliver()`.
Email lands in `me@example.com`. Buffer is cleared. Steps 3–7 repeat every 4 hours.

---

## Files

| File | Owner | What changed |
|---|---|---|
| `cuga_pipelines.yaml` | Developer | Added `type: podcast` data channel block |
| `skills/newsletter_curation.md` | Developer | Added Podcast Highlights section + card template |
| `chat.py` | Developer | +`_DEFAULT_PODCAST_SOURCES`, +`podcast_sources` planner param, +`_apply_defaults` key |
| `agent.py` | Developer | Unchanged |
| `app.py` | Developer | Unchanged |

### cuga++ (the real work)

| File | What was built |
|---|---|
| `packages/cuga-channels/src/cuga_channels/podcast.py` | New `PodcastChannel` DataChannel (~220 lines) |
| `packages/cuga-channels/src/cuga_channels/__init__.py` | Exported `PodcastChannel` |
| `packages/cuga-channels/src/cuga_channels/host.py` | Wired `type: podcast` in `_register_pipeline` |

---

## Configuration reference

### `cuga_pipelines.yaml` podcast block

```yaml
- type: podcast
  default_sources:
    - "https://changelog.com/practicalai/feed"   # Practical AI
    - "https://www.latent.space/feed/podcast"     # Latent Space
  default_keywords:
    - "agent"
    - "agentic"
    - "AI agents"
  max_duration_minutes: 90     # skip episodes over 90 min
  transcript_chars: 2000       # how much transcript to buffer
  whisper_model: tiny          # tiny | base | small | medium | large
  podcast_poll_minutes: 60     # poll frequency
```

### Whisper model tradeoffs

| Model | Speed (30 min ep) | Accuracy | Use for |
|---|---|---|---|
| `tiny` | ~1 min CPU | Low | Testing |
| `base` | ~3 min CPU | Good | Default |
| `small` | ~6 min CPU | Better | Production |
| `medium` | ~15 min CPU | Best local | High quality |
| *(none installed)* | Fast | Good | Falls back to OpenAI Whisper API |

### Environment variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `anthropic` \| `openai` \| `rits` \| `watsonx` |
| `LLM_MODEL` | Model name (e.g. `claude-sonnet-4-6`) |
| `OPENAI_API_KEY` | Used for OpenAI Whisper API fallback if local whisper not installed |
| `RITS_API_KEY` | Used for RITS Whisper API fallback |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | Email delivery |
| `DIGEST_EMAIL` | Default recipient if not specified in chat |

---

## Personas

**Developer** — ships the app, owns `cuga_pipelines.yaml` and `skills/`. Never touches it at runtime.

**End user** — opens `chat.py`, types what they want, done:
```
You:  "watch arxiv and Practical AI podcast for agent research, email me every 4 hours"
CUGA: "Monitor started. 2 RSS feeds, 1 podcast feed, digest every 240 min → me@example.com"

You:  "status"
CUGA: "Monitor active — 2 RSS feeds, 1 podcast, every 240 min, delivery → me@example.com"

You:  "stop"
CUGA: "Monitor stopped."
```
