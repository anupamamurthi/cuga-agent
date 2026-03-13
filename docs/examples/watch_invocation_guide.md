# `cuga watch` — Invocation Guide

Two worked examples with every supported invocation path.

---

## Use Case 1 — Facebook Nanny Finder

> Monitor "Chappaqua Moms" and "Babysitter's Club" Facebook groups for posts
> containing _nanny_, _child care_, or _sitter_. Send an email alert immediately
> on each match.

**Config file:** `watch_config.json`
**Sources:** 2 Facebook groups, polled every 2 minutes via Playwright
**Condition:** keyword match (3 terms)
**Action:** plain `email` — fixed template, no LLM in the dispatch loop

---

### Capabilities

| Capability | This use case |
|---|---|
| Source type | `facebook_group` (Playwright, saved session) |
| Condition | Keyword — 3 terms, case-insensitive |
| Action | `email` — SMTP, fixed subject + body template |
| LLM calls | 1 at startup (NL parse) or 0 (config file) |
| CugaAgent in dispatch | No |
| Agent memory / thread_id | No |
| Archive | Yes — `watch_archive.jsonl` every 2 min |
| Kafka | Not needed (immediate, low-volume) |
| Process model | Single asyncio loop |

---

### All invocation paths

#### 1. CLI — config file (no LLM at all)

```bash
cuga watch --config watch_config.json
```

Loads `WatchConfig` directly from JSON. Starts `WatchExecutor`. No parsing step.

---

#### 2. CLI — natural language

```bash
cuga watch "Watch https://www.facebook.com/groups/603241227268048 \
  and https://www.facebook.com/groups/447824501555433 \
  for nanny or child care or sitter \
  and email anupama.murthi@gmail.com"
```

One LLM call translates the instruction into a `WatchConfig`, then behaves
identically to option 1. Use `--dry-run` to inspect what gets parsed without
starting the monitor:

```bash
cuga watch --dry-run "Watch ... for nanny ... email me"
```

---

#### 3. Python script — explicit wiring

```python
import asyncio, json
from cuga.watch.models import WatchConfig
from cuga.watch.executor import WatchExecutor

config = WatchConfig(**json.load(open("watch_config.json")))
asyncio.run(WatchExecutor(config).run())
```

Full control — no CLI, no agent. Useful when embedding the watcher inside a
larger application.

---

#### 4. CugaAgent tool — `start_watch_from_file`

```python
from cuga import CugaAgent
from cuga.watch.tools import watch_tools

agent = CugaAgent(tools=watch_tools)
await agent.invoke('start the nanny watch from watch_config.json')
# → agent calls: start_watch_from_file("watch_config.json")
```

The agent picks the right tool, starts the watcher as a background asyncio
task, and returns the `watch_id` so you can stop it later.

---

#### 5. CugaAgent tool — `start_watch` (JSON inline)

```python
await agent.invoke(
    'Watch the Chappaqua Moms group for nanny or sitter and email me'
)
# → agent constructs WatchConfig JSON and calls: start_watch(config_json=...)
```

The agent builds the JSON itself from the instruction. No config file needed.

---

#### 6. CugaAgent tool — `start_watch` (NL string direct)

```python
from cuga.watch.tools import start_watch
result = await start_watch.ainvoke({
    "config_json": "Watch Chappaqua Moms for nanny and email anupama@gmail.com"
})
```

`start_watch` accepts the NL string directly — it runs `WatchInstructionParser`
internally if the input is not valid JSON.

---

### Comparison

| Path | LLM calls | Config file | Kafka | Process model | Where CugaAgent appears | What it does |
|---|---|---|---|---|---|---|
| CLI `--config` | 0 | Yes | — | single asyncio | Not involved | — |
| CLI NL | 1 (parse) | No | — | single asyncio | `WatchInstructionParser` calls LLM once at startup | Translates NL instruction → `WatchConfig` JSON |
| Python script | 0 | Yes | — | single asyncio | Not involved | — |
| Agent `start_watch_from_file` | 0 | Yes | — | background task | **Outer agent** interprets request, calls tool | Plans which tool to call; watcher runs agent-free |
| Agent `start_watch` (JSON) | 0 | No | — | background task | **Outer agent** constructs JSON + calls tool | Synthesises config JSON from conversation context |
| Agent `start_watch` (NL) | 1 (parse) | No | — | background task | **Outer agent** calls tool; **inner LLM** in parser | Outer: routes to tool. Inner: NL → `WatchConfig` |

---

---

## Use Case 2 — Gen AI RSS Newsletter

> Monitor 7 generative-AI RSS feeds (arXiv, Hacker News, Hugging Face,
> VentureBeat, Reddit). Filter by 23 AI/LLM keywords. Every hour, invoke
> CugaAgent to curate matches and compose a styled HTML newsletter, then
> deliver it by email.

**Config files:**
- Direct mode: `docs/examples/genai_rss/watch_config_genai_rss.json`
- Kafka mode: `docs/examples/genai_rss/watch_config_genai_rss_kafka.json`

**Sources:** 7 RSS/Atom feeds, polled every 240 minutes
**Condition:** keyword match (23 terms)
**Action:** `agent_notify` — CugaAgent curates + writes HTML, sends via `send_newsletter_email` tool
**Dispatch:** batched — matches accumulate across all 7 sources, drained every 1 minute

---

### Capabilities

| Capability | This use case |
|---|---|
| Source type | `web_page` (RSS/Atom auto-detected) |
| Condition | Keyword — 23 terms, case-insensitive |
| Action | `agent_notify` — LLM composes HTML newsletter |
| LLM calls | 1 at startup (NL parse) or 0; then 1 per dispatch cycle |
| CugaAgent in dispatch | Yes — newsletter editor |
| Agent memory / thread_id | Yes — `watch-genai-rss-watch-01` across all cycles |
| Archive | Disabled in Kafka config; enabled in direct config |
| Kafka | Optional (both modes supported) |
| Process model | Single asyncio (direct) or producer + consumer (Kafka) |

---

### All invocation paths

#### 1. CLI — config file, direct mode

```bash
cuga watch --config docs/examples/genai_rss/watch_config_genai_rss.json
```

Loads `WatchConfig`, starts `WatchExecutor`. CugaAgent auto-created inside the
executor when it detects `agent_notify` actions with SMTP credentials.

---

#### 2. CLI — natural language, direct mode

```bash
cuga watch "monitor arXiv cs.AI, arXiv cs.CL, Hugging Face, Hacker News, \
  VentureBeat, and Reddit ML every 4 hours for LLM, agent, Claude, GPT, \
  Llama, RAG and reasoning mentions — send me a curated HTML newsletter \
  digest every hour"
```

One LLM call to parse, then direct mode. The parser will produce sources and
conditions; the `agent_notify` action won't be inferred unless email credentials
are present in the instruction — use `--config` or set env vars for the full
experience.

---

#### 3. CLI — config file, Kafka mode (auto-detected)

```bash
cuga watch --config docs/examples/genai_rss/watch_config_genai_rss_kafka.json
```

The `"kafka"` key is detected in the file → loaded as `KafkaWatchConfig` →
producer + consumer run concurrently in the same process.

---

#### 4. CLI — natural language, Kafka mode

```bash
cuga watch --kafka \
  "monitor arXiv and Hacker News for LLM and agent mentions, \
   send me a newsletter digest every hour"
```

`--kafka` flag promotes the parsed `WatchConfig` to a `KafkaWatchConfig`
using default broker settings (`localhost:9092`, topic `cuga.watch.events`).

```bash
# Override broker / topic / group:
cuga watch --kafka \
  --kafka-brokers broker1:9092,broker2:9092 \
  --kafka-topic myteam.watch.events \
  --kafka-group rss-consumers-prod \
  "monitor arXiv and HN for LLM mentions, email me hourly"
```

---

#### 5. CLI — Kafka mode, separate processes (production)

```bash
# Terminal 1 — producer only (no LLM, lightweight)
python -m cuga.watch.kafka_producer \
  docs/examples/genai_rss/watch_config_genai_rss_kafka.json

# Terminal 2 — consumer only (CugaAgent + email)
python -m cuga.watch.kafka_consumer \
  docs/examples/genai_rss/watch_config_genai_rss_kafka.json
```

Full process isolation. Producer crashes don't affect the consumer. Consumer
restarts recover from Kafka — no matches lost.

---

#### 6. Python script — direct mode (explicit wiring)

```python
# docs/examples/genai_rss/run.py
python docs/examples/genai_rss/run.py
```

Wires `CugaAgent(tools=[send_newsletter_email])` + `WatchExecutor` explicitly.
`thread_id="genai-rss-watch"` is stable across restarts.

---

#### 7. Python script — Kafka mode (single process, local dev)

```python
# docs/examples/genai_rss/run_kafka.py
python docs/examples/genai_rss/run_kafka.py
```

Producer + consumer via `asyncio.gather`. Convenient for local testing without
running two terminals. Switch to path 5 for production.

Requires a running Kafka broker:
```bash
docker run -d --name kafka -p 9092:9092 apache/kafka:3.7.0
pip install confluent-kafka
```

---

#### 8. NL chat — `chat.py` interactive

```bash
python docs/examples/genai_rss/chat.py
```

Interactive REPL. The agent knows the named configs and can start, stop, and
list watches from plain English:

```
You: start the gen AI RSS watch
CUGA: Watch started. watch_id: a3f9c1b0 ...

You: start the gen AI kafka watch
CUGA: Kafka watch started. watch_id: b7d2e4f1, topic: cuga.watch.events ...

You: list my watches
CUGA: Active watches:
  [running] a3f9c1b0 (thread: genai-rss-watch) — Gen AI RSS Watch

You: stop watch a3f9c1b0
CUGA: Watch a3f9c1b0 stopped.
```

---

#### 9. NL chat — one-shot

```bash
python docs/examples/genai_rss/chat.py "start the gen AI kafka watch"
python docs/examples/genai_rss/chat.py "list my watches"
python docs/examples/genai_rss/chat.py "stop watch a3f9c1b0"
```

---

#### 10. CugaAgent tool — `start_watch_from_file` (direct)

```python
from cuga import CugaAgent
from cuga.watch.tools import watch_tools

agent = CugaAgent(tools=watch_tools)
await agent.invoke(
    'start the gen AI RSS watch from '
    'docs/examples/genai_rss/watch_config_genai_rss.json'
)
# → start_watch_from_file("docs/examples/genai_rss/watch_config_genai_rss.json")
```

---

#### 11. CugaAgent tool — `start_kafka_watch_from_file`

```python
await agent.invoke(
    'start the gen AI kafka watch from '
    'docs/examples/genai_rss/watch_config_genai_rss_kafka.json'
)
# → start_kafka_watch_from_file("docs/examples/genai_rss/watch_config_genai_rss_kafka.json")
```

Producer + consumer started as a single background task. Returns `watch_id`.

---

#### 12. CugaAgent tool — `start_kafka_watch` (NL, no config file)

```python
await agent.invoke(
    'monitor arXiv and Hacker News for LLM and agent mentions '
    'and send me a newsletter digest every hour via Kafka on localhost:9092'
)
# → start_kafka_watch(
#     config_json="monitor arXiv and Hacker News ...",
#     bootstrap_servers="localhost:9092"
#   )
```

`start_kafka_watch` detects non-JSON input → runs `WatchInstructionParser` →
injects Kafka block → starts producer + consumer. No config file required.

---

### Comparison

| Path | Mode | LLM (parse) | LLM (dispatch) | Config file | Process model | Where CugaAgent appears | What it does |
|---|---|---|---|---|---|---|---|
| CLI `--config` (direct) | direct | 0 | 1 / cycle | Yes | single asyncio | **Dispatch** — `agent_notify` in `WatchExecutor` | Curates matched items, writes HTML newsletter, calls `send_newsletter_email` tool |
| CLI NL (direct) | direct | 1 | 1 / cycle | No | single asyncio | **Parse** (`WatchInstructionParser`) + **dispatch** | Parse: NL → `WatchConfig`. Dispatch: newsletter composition |
| CLI `--config` (Kafka auto-detect) | kafka | 0 | 1 / cycle | Yes | single asyncio | **Consumer dispatch** — `KafkaWatchConsumer` | Same as direct dispatch; reads batched matches from Kafka buffer |
| CLI `--kafka` NL | kafka | 1 | 1 / cycle | No | single asyncio | **Parse** + **consumer dispatch** | Parse: NL → `WatchConfig` + Kafka block. Dispatch: newsletter |
| `python -m kafka_producer` + `kafka_consumer` | kafka | 0 | 1 / cycle | Yes | 2 processes | **Consumer process only** — producer has no agent | Consumer's `CugaAgent` curates + emails; producer is pure Python |
| `run.py` | direct | 0 | 1 / cycle | Yes | single asyncio | **Dispatch** — explicitly wired in script | Same as CLI direct dispatch; `thread_id` hardcoded as `"genai-rss-watch"` |
| `run_kafka.py` | kafka | 0 | 1 / cycle | Yes | single asyncio | **Consumer dispatch** — explicitly wired in script | Consumer's `CugaAgent` curates + emails; stable `thread_id` from config |
| `chat.py` interactive | both | 0 | 1 / cycle | Yes (named) | background task | **Three roles**: conversation, tool routing, dispatch | Converses with user; calls `start_watch*` tool; inner dispatch agent curates newsletter |
| `chat.py` one-shot | both | 0 | 1 / cycle | Yes (named) | background task | Same three roles, one turn | Interprets utterance, routes to tool, dispatch agent handles newsletters |
| Agent `start_watch_from_file` | direct | 0 | 1 / cycle | Yes | background task | **Outer**: tool routing. **Inner dispatch**: newsletter | Outer plans + calls tool. Inner (in executor) curates + emails per cycle |
| Agent `start_kafka_watch_from_file` | kafka | 0 | 1 / cycle | Yes | background task | **Outer**: tool routing. **Consumer**: newsletter | Outer starts both producer+consumer. Consumer's agent handles dispatch |
| Agent `start_kafka_watch` (NL) | kafka | 1 | 1 / cycle | No | background task | **Outer**: routing + parse. **Consumer**: newsletter | Outer routes + triggers NL parse. Consumer's agent curates + emails |

---

### What "1 / cycle" means for LLM dispatch

`dispatch_interval_minutes = 1` in the Kafka config. The CugaAgent is invoked
**once per drain cycle**, only when the buffer is non-empty. If no sources
produced keyword matches that minute, no LLM call is made. At 4-hour poll
intervals the LLM is called at most once per hour (the drain fires 60 times per
poll window, but only the first drain after a poll cycle will have matches).

---

### Thread ID and agent memory

All invocation paths for this use case converge on `thread_id = "watch-genai-rss-watch-01"`
(set explicitly in the Kafka config; hardcoded as `"genai-rss-watch"` in `run.py`).

This means:
- The agent accumulates context across dispatch cycles: "3rd arXiv chain-of-thought paper this week"
- If the consumer restarts, the `thread_id` is recovered from the next Kafka message and agent memory continues uninterrupted
- All invocation paths that use the same config file share the same agent memory
