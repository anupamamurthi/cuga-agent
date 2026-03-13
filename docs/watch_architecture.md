# `cuga watch` Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        cuga watch CLI                           │
│                                                                 │
│   Natural-language instruction  OR  --config watch_config.json  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ (one-time, at startup)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   WatchInstructionParser                        │
│                                                                 │
│   LLM call (WatsonX/OpenAI)  →  structured JSON  →  WatchConfig│
│   Falls back to regex heuristic if LLM unavailable             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       WatchConfig                               │
│                                                                 │
│   sources: [ { type, url, interval_minutes, extra } ]           │
│   condition: { type: keyword | always | custom, keywords: [] }  │
│   actions: [ { type: email | sms | log | agent_notify } ]       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ wired by WatchExecutor
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CugaWatcher                               │
│                  (asyncio event loop)                           │
│                                                                 │
│  ┌────────────────────────────────────────────────┐            │
│  │  @watcher.source(every_minutes=30)             │            │
│  │                                                │            │
│  │  facebook_group ──► Playwright scraper         │            │
│  │  web_page       ──► httpx fetch                │  emit      │
│  │  rss_feed       ──► httpx + XML parser         ├───────►    │
│  │                                                │  list[dict]│
│  └────────────────────────────────────────────────┘            │
│                                                                 │
│  ┌────────────────────────────────────────────────┐            │
│  │  @watcher.on(source, when=predicate)           │ ◄──────    │
│  │                                                │            │
│  │  WatchCondition.matches?                       │            │
│  │  ┌─ keyword:  any kw in item.text?             │            │
│  │  ├─ always:   non-empty?                       │            │
│  │  └─ custom:   eval(expression)?                │            │
│  │                                                │            │
│  │  ──► dispatch WatchActions ──────────────────► │            │
│  │       email (SMTP/Gmail)                       │            │
│  │       sms   (Twilio)                           │            │
│  │       log   (stdout)                           │            │
│  │       agent_notify → CugaAgent.invoke()        │            │
│  │                       (thread_id for memory)   │            │
│  └────────────────────────────────────────────────┘            │
│                                                                 │
│  ┌────────────────────────────────────────────────┐            │
│  │  archive drain (every 2 min)                   │            │
│  │  buffer → watch_archive.jsonl                  │            │
│  └────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

## Native CUGA integration — Level 1 + Level 2

```
┌─────────────────────────────────────────────────────────────────┐
│  Level 1 — Watch as a CugaAgent tool                           │
│                                                                 │
│  user: "Watch Chappaqua Moms for nanny posts and email me"      │
│           ↓                                                     │
│  CugaAgent (task decomposition + planning)                      │
│           ↓  tool call                                          │
│  start_watch(config_json, thread_id)  ◄── @tool                 │
│           ↓                                                     │
│  WatchExecutor launched as asyncio background task              │
│           ↓                                                     │
│  WatchManager registry  { watch_id → task + thread_id }        │
│                                                                 │
│  stop_watch(watch_id)   ◄── @tool                               │
│  list_watches()         ◄── @tool                               │
└──────────────────────────────┬──────────────────────────────────┘
                               │ on keyword match
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Level 2 — Events feed back into the agent (persistent memory) │
│                                                                 │
│  agent_notify action fires                                      │
│           ↓                                                     │
│  CugaAgent.invoke(task, thread_id="watch-abc123")               │
│           ↓                                                     │
│  same thread_id → agent accumulates context across events       │
│  "this is the 3rd nanny post this week — high urgency"          │
└─────────────────────────────────────────────────────────────────┘
```

## End-to-end data flow — question to email

Tracing one complete journey: *"Watch Chappaqua Moms for nanny posts and email me"* → email in your inbox.

```
USER INPUT
──────────
"Watch https://www.facebook.com/groups/603241227268048
 for nanny or child care or sitter
 and email anupama.murthi@gmail.com"

          │
          │  1. PARSE  (one LLM call, happens once at startup)
          ▼
┌─────────────────────────────────────────────────────────┐
│  WatchInstructionParser._parse_with_llm()               │
│                                                         │
│  System prompt + schema hint sent to WatsonX/OpenAI     │
│  LLM returns JSON:                                      │
│  {                                                      │
│    "sources": [{                                        │
│      "type": "facebook_group",                          │
│      "url":  "https://facebook.com/groups/603...",      │
│      "name": "Chappaqua Moms",                          │
│      "interval_minutes": 30,                            │
│      "extra": { "session_file": "fb_session.json" }     │
│    }],                                                  │
│    "condition": {                                       │
│      "type": "keyword",                                 │
│      "keywords": ["nanny","child care","sitter"]        │
│    },                                                   │
│    "actions": [{                                        │
│      "type": "email",                                   │
│      "smtp_host": "smtp.gmail.com",                     │
│      "smtp_port": 587,                                  │
│      "email_to": "anupama.murthi@gmail.com"             │
│    }]                                                   │
│  }                                                      │
└──────────────────────────┬──────────────────────────────┘
                           │  WatchConfig (Pydantic model, validated)
                           │
                           │  2. WIRE  (WatchExecutor.run)
                           ▼
┌─────────────────────────────────────────────────────────┐
│  WatchExecutor registers sources + handlers on           │
│  CugaWatcher (asyncio event loop)                       │
│                                                         │
│  source task:   every 30 min → _fetch_facebook_group()  │
│  handler task:  on emit      → _notify_handler()        │
│  archive task:  every 2 min  → drain buffer to .jsonl   │
└──────────────────────────┬──────────────────────────────┘
                           │
                           │  3. POLL  (every 30 minutes)
                           ▼
┌─────────────────────────────────────────────────────────┐
│  _fetch_facebook_group()                                │
│                                                         │
│  Playwright launches headless Chromium                  │
│  Loads fb_session.json  (saved login cookies)           │
│  Navigates to facebook.com/groups/603241227268048        │
│  Scrolls page 3× to load more posts                     │
│  Extracts all [role="article"] elements                 │
│                                                         │
│  Returns list[dict]:                                    │
│  [                                                      │
│    { "text": "Anyone know a good nanny in Chappaqua?",  │
│      "url":  "https://facebook.com/groups/603...",      │
│      "source_name": "Chappaqua Moms" },                 │
│    { "text": "School pickup help needed this week...",  │
│      ... },                                             │
│    ...up to 20 items                                    │
│  ]                                                      │
└──────────────────────────┬──────────────────────────────┘
                           │  list[dict] emitted to asyncio queue
                           │
                           │  4. FILTER
                           ▼
┌─────────────────────────────────────────────────────────┐
│  _predicate(items)  →  _matches(items, condition)       │
│                                                         │
│  For each item, check: does item["text"].lower()        │
│  contain "nanny", "child care", or "sitter"?            │
│                                                         │
│  ✗  "School pickup help needed..." → no match, skip     │
│  ✓  "Anyone know a good nanny..."  → MATCH              │
│                                                         │
│  predicate returns True → handler fires                 │
└──────────────────────────┬──────────────────────────────┘
                           │
                           │  5. EXTRACT MATCHES
                           ▼
┌─────────────────────────────────────────────────────────┐
│  _filter_matches(items, condition)                      │
│                                                         │
│  Keeps only matching items, annotates with which        │
│  keyword(s) triggered the match:                        │
│                                                         │
│  [                                                      │
│    { "text":             "Anyone know a good nanny?",   │
│      "source_name":      "Chappaqua Moms",              │
│      "url":              "https://facebook.com/...",    │
│      "matched_keywords": ["nanny"] }                    │
│  ]                                                      │
└──────────────────────────┬──────────────────────────────┘
                           │
                           │  6. BUILD EMAIL BODY
                           ▼
┌─────────────────────────────────────────────────────────┐
│  _dispatch_action()  builds subject + body              │
│                                                         │
│  Subject:                                               │
│  "Watch alert: nanny in Chappaqua Moms"                 │
│                                                         │
│  Body:                                                  │
│  [2024-03-10 22:30] 1 match(es) found                   │
│                                                         │
│  Source:   Chappaqua Moms                               │
│  Keywords: nanny                                        │
│  Excerpt:  Anyone know a good nanny in Chappaqua?...    │
└──────────────────────────┬──────────────────────────────┘
                           │
                           │  7. SEND EMAIL
                           ▼
┌─────────────────────────────────────────────────────────┐
│  _send_email(action, subject, body)                     │
│                                                         │
│  username ← WATCH_EMAIL_USERNAME env var                │
│  password ← WATCH_EMAIL_PASSWORD env var (App Password) │
│                                                         │
│  smtplib.SMTP("smtp.gmail.com", 587)                    │
│    .starttls()                                          │
│    .login(username, password)                           │
│    .sendmail(from, "anupama.murthi@gmail.com", msg)     │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
              📬  Email arrives in inbox
              Subject: "Watch alert: nanny in Chappaqua Moms"

                           │
                           │  (loop continues)
                           │  wait 30 minutes → go back to step 3
                           ▼
                         POLL AGAIN
```

### Data shapes at each step

| Step | Data type | Example value |
|------|-----------|---------------|
| User input | `str` | `"Watch ... for nanny ... email me"` |
| After parse | `WatchConfig` | Pydantic model with sources, condition, actions |
| After scrape | `list[dict]` | `[{"text": "...", "url": "...", "source_name": "..."}]` |
| After filter | `list[dict]` | Same + `"matched_keywords": ["nanny"]` |
| Email subject | `str` | `"Watch alert: nanny in Chappaqua Moms"` |
| Email body | `str` | Formatted match summary with timestamp + excerpt |

---

## How it works

1. **Parse once** — at startup the LLM converts your natural-language instruction into a typed `WatchConfig`. No further LLM calls (unless you use `agent_notify`).
2. **Poll on a timer** — each source fires on its own `interval_minutes` schedule. Sources are independent asyncio tasks.
3. **Filter** — each emission passes through `WatchCondition`. Only matching items flow to actions.
4. **Act** — matching items are dispatched to every configured action.
5. **Archive** — all raw items are buffered in memory and flushed to `watch_archive.jsonl` every 2 minutes.
6. **Agent memory (Level 2)** — `agent_notify` actions invoke `CugaAgent` on a stable `thread_id`, so the agent accumulates reasoning context across multiple match events.

## Kafka mode — distributed deployment

For production or multi-consumer use cases, the same `WatchConfig` can run in a decoupled producer/consumer architecture.

```
┌──────────────────────────────────┐      ┌──────────────────────────────────┐
│  PRODUCER (Process 1)            │      │  CONSUMER (Process 2)            │
│                                  │      │                                  │
│  Source 1 (every N min) ─┐       │      │                                  │
│  Source 2 (every N min) ─┤       │      │  ┌──────────────────────────┐    │
│  Source 3 (every N min) ─┤       │      │  │  match_buffer: list[dict]│    │
│                           │      │      │  │  (accumulates across     │    │
│  for each source:         │      │      │  │   all messages)          │    │
│   _fetch_source()         │      │      │  └────────────┬─────────────┘    │
│   _filter_matches()       │      │      │               │ every N min      │
│   → WatchEvent JSON       │      │      │               ▼                  │
│     {                     │      │      │  dispatch_interval > 0:          │
│       watch_id,           │─────►│Kafka │  CugaAgent.invoke(              │
│       thread_id,          │topic │      │    matches, thread_id)           │
│       source_name,        │      │      │    → _send_html_email()          │
│       matches: [...]      │      │      │                                  │
│     }                     │      │      │  dispatch_interval == 0:         │
│                           │      │      │  per-message immediate dispatch  │
└──────────────────────────────────┘      └──────────────────────────────────┘

           topic:          cuga.watch.events
           consumer group: defined in KafkaWatchConfig
           message format: WatchEvent (JSON, one per source per poll cycle)
```

**What changes in Kafka mode vs direct mode:**

| Concern | Direct (WatchExecutor) | Kafka |
|---|---|---|
| **Process model** | Single asyncio loop, poll + dispatch together | Producer and consumer are independent processes |
| **Crash resilience** | Crash stops both polling and dispatch | Each restarts independently; messages persist until consumed |
| **thread_id continuity** | Held in executor memory | Carried in every `WatchEvent` message — survives consumer restart |
| **Scale-out** | One dispatch handler | Multiple consumer groups can subscribe simultaneously |
| **Observability** | In-process only | Every match batch is a Kafka message — replayable and inspectable |

**To run:**
```bash
# Terminal 1 — producer
python -m cuga.watch.kafka_producer watch_config.json

# Terminal 2 — consumer
python -m cuga.watch.kafka_consumer watch_config.json

# or via NL chat (starts both):
start_kafka_watch_from_file("watch_config.json")   # via CugaAgent tool
```

---

## Key files

| File | Role |
|------|------|
| `src/cuga/watch/models.py` | Pydantic models: `WatchConfig`, `WatchSource`, `WatchCondition`, `WatchAction` |
| `src/cuga/watch/parser.py` | LLM + heuristic parser: natural language → `WatchConfig` |
| `src/cuga/watch/executor.py` | Source adapters, condition filters, action dispatchers, `WatchExecutor` |
| `src/cuga/watch/tools.py` | `@tool` functions: `start_watch`, `stop_watch`, `list_watches` + `WatchManager` |
| `src/cuga/watch/kafka_models.py` | `KafkaWatchConfig` — extends `WatchConfig` with Kafka connectivity + watch/thread IDs |
| `src/cuga/watch/kafka_producer.py` | `KafkaWatchProducer` — polls sources, filters, publishes `WatchEvent` JSON to Kafka |
| `src/cuga/watch/kafka_consumer.py` | `KafkaWatchConsumer` — reads topic, buffers, dispatches via same action pipeline |
| `src/cuga/watcher.py` | `CugaWatcher` — asyncio event loop with `@source` / `@on` decorators |
| `src/cuga/cli.py` | `cuga watch` CLI entry point |

## Extending

### New source type

Add a fetch function in `executor.py` and a new branch in `_fetch_source()`:

```python
async def _fetch_source(source: WatchSource) -> list[dict]:
    if source.type == "facebook_group":
        return await _fetch_facebook_group(source)
    if source.type == "slack_channel":   # new
        return await _fetch_slack(source)
    return await _fetch_web_page(source)
```

Also add the new literal to `WatchSource.type` in `models.py`.

### New action type

Add a branch in `_dispatch_action()` in `executor.py`:

```python
elif action.type == "webhook":           # new
    await _post_webhook(action, body)
```

Also add the new literal to `WatchAction.type` in `models.py`.

### New condition type

Add a branch in `_matches()` and `_filter_matches()` in `executor.py`:

```python
if condition.type == "regex":
    import re
    return any(re.search(condition.expression, item.get("text", "")) for item in items)
```

## Watching multiple sources

Add multiple entries to `sources` in your `watch_config.json`. Each source polls independently on its own schedule:

```json
{
  "sources": [
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/GROUP_A",
      "name": "Group A",
      "interval_minutes": 30,
      "extra": { "session_file": "./fb_session.json" }
    },
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/GROUP_B",
      "name": "Group B",
      "interval_minutes": 15,
      "extra": { "session_file": "./fb_session.json" }
    },
    {
      "type": "rss_feed",
      "url": "https://example.com/feed.rss",
      "name": "Local RSS",
      "interval_minutes": 10
    }
  ]
}
```

The same `condition` and `actions` apply to all sources. Each source gets its own watcher task and its own notification handler.
