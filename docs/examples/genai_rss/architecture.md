# Gen AI RSS Watch — Use Case & Architecture

## The Problem

Keeping up with generative AI is a full-time job. New papers land on arXiv daily. Model releases happen weekly. Hacker News, Reddit, VentureBeat, and Hugging Face each produce dozens of AI-related posts every few hours. Manually scanning all of these sources is:

- **Time-consuming** — 7+ feeds, each updating multiple times a day
- **Noisy** — most content doesn't warrant attention
- **Fragmented** — each source has a different format and signal quality
- **Reactive** — you only see things when you go looking

**The goal:** automatically monitor all relevant feeds, filter for what matters, and receive a single curated newsletter digest — written by an AI editor — delivered to your inbox, hands-free.

---

## Use Case: AI Research Newsletter Watch

> "Monitor arXiv, Hacker News, Hugging Face, VentureBeat, and Reddit for LLM/agent research, model releases, and any mentions of CUGA or ALTK. Send me one curated HTML newsletter digest every hour."

### Sources Monitored (every 4 hours)

| Feed | What it covers |
|---|---|
| arXiv cs.AI | Academic AI papers |
| arXiv cs.CL | NLP, LLM, language model research |
| Hugging Face Blog | Model releases, technical deep-dives |
| Hacker News | Community discussion, industry news |
| Hacker News — AI Agents | Filtered HN posts about agents specifically |
| VentureBeat AI | Industry news, product launches, funding |
| Reddit r/MachineLearning | Community commentary, paper discussions |

### What Triggers a Newsletter

Any item containing keywords from this list (case-insensitive):

> CUGA · ALTK · LLM · large language model · GPT · Claude · Gemini · Llama · Mistral · Qwen · agent · agentic · multi-agent · multimodal · diffusion · foundation model · fine-tuning · RAG · retrieval-augmented · reasoning · chain-of-thought · transformer · attention mechanism · open source model

---

## Architecture Overview

There are two deployment modes for this use case. Both produce the same output — a single curated newsletter per cycle — but differ in how polling and dispatch are structured.

---

### Mode 1: Direct (Single Process)

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   SOURCES                FILTER            BUFFER         CUGA      │
│                                                                     │
│   arXiv cs.AI ──┐                                                   │
│   arXiv cs.CL ──┤                                                   │
│   Hugging Face ─┤  every   ┌──────────┐   ┌────────┐  every 1 min  │
│   Hacker News ──┼──240min─►│ Keyword  │──►│ Match  │──────────────►│
│   HN AI Agents ─┤  poll    │ Filter   │   │ Buffer │               │
│   VentureBeat ──┤          └──────────┘   └────────┘               │
│   Reddit ML ────┘                                                   │
│                                          CugaAgent  _send_html_email│
│                                          (composes) ──────────────► │
│                                           HTML                📧    │
└─────────────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. `CugaWatcher` registers 7 async polling tasks, one per source
2. Each task fetches the RSS feed every 240 minutes
3. Python keyword filter checks each item — no LLM involved at this stage
4. Matching items accumulate in a shared in-memory buffer
5. Every 1 minute, the buffer is drained
6. `CugaAgent` receives all buffered matches and composes an HTML newsletter
7. Python SMTP sends the email directly — no LLM involvement in the send

**To run:**
```bash
python docs/examples/genai_rss/run.py
# or via NL chat:
python docs/examples/genai_rss/chat.py "start the gen AI RSS watch"
```

---

### Mode 2: Kafka (Distributed / Decoupled)

```
┌──────────────────────────────┐      ┌──────────────────────────────┐
│   PRODUCER (Process 1)       │      │   CONSUMER (Process 2)       │
│                              │      │                              │
│  arXiv cs.AI ──┐             │      │  ┌─────────────────────┐     │
│  arXiv cs.CL ──┤   Keyword   │      │  │  Message Buffer      │     │
│  Hugging Face ─┼──►Filter ──►│─────►│  │  (across sources)   │     │
│  Hacker News ──┤   (Python)  │Kafka │  └──────────┬──────────┘     │
│  HN AI Agents ─┤             │topic │             │ every 1 min    │
│  VentureBeat ──┤             │      │             ▼                │
│  Reddit ML ────┘             │      │        CugaAgent             │
│                              │      │        (composes HTML)       │
│  1 message per source        │      │             │                │
│  per poll cycle              │      │   _send_html_email (Python)  │
│                              │      │             │                │
└──────────────────────────────┘      │             ▼                │
                                      │           📧 Gmail            │
                                      └──────────────────────────────┘

                       Kafka topic:  cuga.watch.events
                       Consumer group:  cuga-genai-rss-consumers
```

**Data flow:**
1. Producer runs identically to direct mode — polls 7 feeds, keyword filters, but instead of buffering locally it **publishes each match batch as a JSON message to Kafka**
2. Consumer reads messages from the topic and accumulates them in its own buffer
3. Every 1 minute, consumer drains the buffer — same CugaAgent → email path as direct mode

**To run:**
```bash
# Terminal 1 — producer
python -m cuga.watch.kafka_producer docs/examples/genai_rss/watch_config_genai_rss_kafka.json

# Terminal 2 — consumer
python -m cuga.watch.kafka_consumer docs/examples/genai_rss/watch_config_genai_rss_kafka.json

# or via NL chat (starts both):
python docs/examples/genai_rss/chat.py "start the gen AI kafka watch"
```

---

## The Three Layers — What Each One Does

### Layer 1: The Scheduler — CugaWatcher

`CugaWatcher` is CUGA's proactive scheduling primitive. Normally CUGA is **reactive** — you ask it something, it responds. `CugaWatcher` makes it **proactive**: it fires on a timer without any human input.

```python
@watcher.source(every_minutes=240)
async def fetch_arxiv():
    return fetch_rss("https://arxiv.org/rss/cs.AI")

@watcher.on(fetch_arxiv, when=lambda items: has_keywords(items))
async def handle_matches(items):
    buffer.extend(items)
```

- Each source is an **independent async task** running on its own schedule
- The `when=` predicate is the keyword filter — the handler only fires if at least one item matches
- All sources share one event queue; the dispatcher fans out to registered handlers
- A separate **drain source** fires every 1 minute and triggers the newsletter when the buffer is non-empty

**Key property:** CugaWatcher is purely event-driven. No polling loops, no busy-waiting. Each source sleeps until its next scheduled time.

---

### Layer 2: The Intelligence — CugaAgent

`CugaAgent` is CUGA's LLM-powered agent. In this use case it serves as the **newsletter editor** — the part that requires human-like judgment that no keyword filter can replicate.

Without intelligence, you could only send a raw data dump of 100+ matched items. With CugaAgent:

| What the agent does | Why it matters |
|---|---|
| **Curation** | Filters out noise — generic blog posts, reposts, low-signal items |
| **Prioritisation** | Breakthrough research and model releases first; community chatter last |
| **CUGA/ALTK alerting** | Flags any mentions of the frameworks in a dedicated "On The Radar" section |
| **Categorisation** | Sorts items: Research Papers / Industry & Products / Community Buzz |
| **Summarisation** | 1–2 sentence summaries per item — the right density for a newsletter |
| **HTML authoring** | Produces a styled newsletter with inline CSS, dark header, hyperlinked titles |

The agent receives matched items as a Python variable (`matched_items`) and writes code that builds the HTML. The resulting HTML is extracted from the agent's output and handed directly to the Python email sender.

**The LLM is called exactly once per dispatch cycle** — only when there is something worth sending.

---

### Layer 3: The Delivery — Python SMTP

Email delivery is handled directly by Python's `smtplib` — no LLM involvement. This separation is intentional:

- Email sending is **deterministic** and must never fail due to model behaviour
- Separating composition (CugaAgent) from delivery (Python) means a failed send doesn't waste an LLM call, and vice versa
- SMTP credentials stay in the config file; the agent never sees or handles them

---

## Why Kafka?

In direct mode, a single Python process handles both polling and dispatch. This works well but has limitations at scale:

| Concern | Direct Mode | Kafka Mode |
|---|---|---|
| **Process isolation** | Polling + email in one process — crash stops both | Producer and consumer restart independently |
| **Reliability** | If the process is down during a poll, matches are lost | Messages persist in the topic until the consumer catches up |
| **Scalability** | One process, one consumer | Multiple consumers can subscribe — e.g. email + Slack simultaneously |
| **Separation of concerns** | RSS polling and LLM calls share the same process | Lightweight producer (no LLM) and heavier consumer (LLM) can run on separate machines |
| **Observability** | Harder to inspect what was matched and when | Every match batch is a Kafka message — replayable, inspectable, and auditable |

For a personal newsletter bot, direct mode is sufficient. For a team or production deployment, Kafka makes each component independently deployable and resilient.

---

## End-to-End Workflow (Direct Mode)

```
T=0:00   CugaWatcher starts
         → 7 source tasks scheduled, first poll runs immediately

T=0:01   arXiv cs.AI polled (30 items fetched)
         → 18 items match keywords → buffered

T=0:02   arXiv cs.CL polled (30 items fetched)
         → 22 items match → buffered

         ... (5 more sources polled in parallel)

T=0:04   All 7 sources polled. Buffer: ~120 matched items total.

T=1:00   Drain timer fires (every 1 min, first after 1 full interval)
         → CugaAgent invoked with matched_items (120 items)
         → Agent curates: selects ~15 significant items
         → Agent writes HTML newsletter:

              ┌───────────────────────────────────────┐
              │  AI Watch Digest — 2026-03-12          │  ← dark header
              ├───────────────────────────────────────┤
              │  🔍 On The Radar                       │  ← only if CUGA/ALTK found
              │  · CUGA mentioned in arXiv paper...   │
              ├───────────────────────────────────────┤
              │  ✨ Highlights                         │
              │  · GPT-5 announced by OpenAI          │
              │  · Llama 4 released with MoE arch...  │
              ├───────────────────────────────────────┤
              │  📄 Research Papers                    │
              │  🏭 Industry & Products                │
              │  💬 Community Buzz                     │
              ├───────────────────────────────────────┤
              │  AI Watch Digest                       │  ← footer
              └───────────────────────────────────────┘

         → Python SMTP sends HTML email via Gmail

T=4:05   Sources poll again (second cycle). New items buffered.
T=5:05   Drain fires. Newsletter #2 sent if new matches found.
```

---

## Configuration — One JSON File

The entire watch is defined in a single config file. No code changes required to add sources, change keywords, or adjust timing.

```json
{
  "description": "Monitor generative AI RSS feeds...",
  "sources": [
    { "url": "https://arxiv.org/rss/cs.AI", "interval_minutes": 240 },
    { "url": "https://news.ycombinator.com/rss", "interval_minutes": 240 }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["LLM", "agent", "CUGA", "Claude", "GPT"]
  },
  "actions": [{
    "type": "agent_notify",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_username": "you@gmail.com",
    "smtp_password": "<app-password>",
    "email_to": "you@gmail.com"
  }],
  "dispatch_interval_minutes": 60
}
```

For Kafka mode, add one block and the rest is identical:
```json
{
  "kafka": {
    "bootstrap_servers": "localhost:9092",
    "topic": "cuga.watch.events",
    "group_id": "cuga-genai-rss-consumers"
  }
}
```

---

## File Structure

```
docs/examples/genai_rss/
├── watch_config_genai_rss.json        ← Direct mode config
├── watch_config_genai_rss_kafka.json  ← Kafka mode config
├── run.py                             ← Direct mode runner
├── run_kafka.py                       ← Kafka mode runner (producer + consumer)
├── chat.py                            ← NL chat interface (start/stop via plain English)
└── architecture.md                    ← This document

src/cuga/watch/
├── models.py          ← WatchConfig, WatchSource, WatchCondition, WatchAction
├── executor.py        ← WatchExecutor: wires config → CugaWatcher + CugaAgent
├── tools.py           ← LangChain tools: start_watch, stop_watch, list_watches
├── kafka_models.py    ← KafkaWatchConfig (extends WatchConfig)
├── kafka_producer.py  ← KafkaWatchProducer: polls feeds, publishes to topic
└── kafka_consumer.py  ← KafkaWatchConsumer: reads topic, buffers, dispatches

src/cuga/
└── watcher.py         ← CugaWatcher: event-driven async scheduler
```

---

## Summary

| What | How |
|---|---|
| **Problem** | Too many AI feeds, too much noise, no time to read everything |
| **Solution** | Automated monitoring + AI curation → one newsletter per cycle |
| **Scheduler** | `CugaWatcher` — event-driven async polling, one task per source |
| **Filter** | Python keyword matching — fast, zero LLM cost for noise rejection |
| **Intelligence** | `CugaAgent` — curates, summarises, categorises, writes the HTML |
| **Delivery** | Python SMTP — deterministic, no LLM dependency for the send |
| **Scale-out** | Kafka — decouple producer from consumer, replay on failure |
| **Config** | One JSON file — no code changes to add sources or keywords |
| **Invocation** | Plain English via `chat.py` — "start the gen AI RSS watch" |
