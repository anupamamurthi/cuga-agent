# Facebook Group Nanny Finder — Use Case & Architecture

## The Problem

Finding a nanny or child care provider through local Facebook groups is a manual, time-sensitive process. Posts offering or asking about nannies, babysitters, and child care appear unpredictably throughout the day across multiple community groups. Checking manually means:

- **Missing posts** — a great candidate posts at 2am and is gone by morning
- **Constant checking** — opening Facebook repeatedly through the day just in case
- **Group overload** — community groups mix nanny posts with hundreds of unrelated posts
- **No filtering** — you have to read every post to find the relevant ones

**The goal:** automatically watch multiple Facebook community groups around the clock, instantly detect posts about nanny or child care, and send an alert the moment a relevant post appears — without ever opening Facebook manually.

---

## Use Case: Nanny Finder Alert

> "Watch the Chappaqua Moms and Babysitter's Club Facebook groups every 2 minutes. Alert me immediately by email whenever someone posts about a nanny, sitter, or child care."

### Groups Monitored (every 2 minutes)

| Group | What it covers |
|---|---|
| Chappaqua Moms (`/groups/603241227268048`) | Local community group — nanny referrals, recommendations, job postings |
| Babysitter's Club (`/groups/447824501555433`) | Local group focused on childcare and sitting arrangements |

### What Triggers an Alert

Any post containing (case-insensitive):

> **nanny** · **child care** · **sitter**

### What You Receive

An immediate email alert with:
- Which group the post came from
- The full post text
- A direct link to the post on Facebook

---

## Architecture Overview

There are two deployment modes. Both trigger the same email alert, but differ in how the Facebook polling and notification dispatch are connected.

---

### Mode 1: Direct (Single Process)

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│   FACEBOOK GROUPS          FILTER          DEDUP          NOTIFY    │
│                                                                      │
│   Chappaqua Moms ──┐                                                │
│                    │  every   ┌──────────┐ ┌──────────┐            │
│                    ├──2 min──►│ Keyword  │►│ Seen     │► email     │
│                    │  poll    │ Filter   │ │ Posts    │  (Python   │
│   Babysitter's     │          │ (Python) │ │ (SHA-1)  │   SMTP)    │
│   Club ────────────┘          └──────────┘ └──────────┘            │
│                                                                      │
│   Also every 2 min:                                                  │
│   matched posts ──────────────────────────────► archive.jsonl       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. `CugaWatcher` registers 2 async polling tasks, one per Facebook group
2. Each task launches a headless Chromium browser (Playwright) every 2 minutes, authenticates using a saved session, scrolls the group feed, and extracts post text + permalink URLs
3. New posts are deduplicated via SHA-1 hash (group URL + post text) stored in `seen_posts.json` — previously seen posts are skipped
4. Remaining posts are checked for keywords — no LLM involved at this stage
5. Matching posts trigger an immediate email alert via Python SMTP
6. A parallel drain task archives all fetched posts to `posts_archive.jsonl` every 2 minutes

**To run:**
```bash
# One-time: save your Facebook login session
python docs/examples/facebook_monitor/setup_session.py

# Then start the monitor
python docs/examples/facebook_monitor/event_monitor.py
```

---

### Mode 2: Kafka (Distributed / Decoupled)

```
┌──────────────────────────────┐      ┌──────────────────────────────┐
│   PRODUCER (Process 1)       │      │   CONSUMER (Process 2)       │
│                              │      │                              │
│  Chappaqua Moms ──┐          │      │  ┌──────────────────────┐    │
│                   │ Keyword  │      │  │ _dispatch_action      │    │
│                   ├──Filter─►│─────►│  │ (email / sms / log)  │    │
│  Babysitter's     │ (Python) │Kafka │  └──────────────────────┘    │
│  Club ────────────┘          │topic │                              │
│                              │      │  One action per message.     │
│  1 message per matching      │      │  No buffering — nanny posts   │
│  post batch per group poll   │      │  are time-sensitive.         │
│                              │      │                              │
└──────────────────────────────┘      └──────────────────────────────┘

                       Kafka topic:  cuga.watch.events
                       Consumer group:  cuga-watch-consumers
```

**Data flow:**
1. Producer runs identically to direct mode — polls Facebook groups, deduplicates, keyword filters — but instead of dispatching locally it **publishes each match batch as a JSON message to Kafka**
2. Consumer reads messages from the topic and dispatches immediately — no batching buffer, because nanny alerts are time-sensitive and you want notification as fast as possible
3. Each Kafka message is a self-contained event with the matched posts, source group name, and a stable `thread_id` for agent memory continuity

**To run:**
```bash
# Terminal 1 — start Kafka (Docker)
docker run -p 9092:9092 apache/kafka:3.7.0

# Terminal 2 — producer
python -m cuga.watch.kafka_producer docs/examples/kafka/watch_config_kafka.json

# Terminal 3 — consumer
python -m cuga.watch.kafka_consumer docs/examples/kafka/watch_config_kafka.json
```

---

## The Three Layers — What Each One Does

### Layer 1: The Scheduler — CugaWatcher

`CugaWatcher` is CUGA's proactive scheduling primitive. Normally CUGA is **reactive** — you ask it something, it responds. `CugaWatcher` makes it **proactive**: it fires on a timer without any human input.

```python
@watcher.source(every_minutes=2)
async def fetch_chappaqua():
    return await _fetch_facebook_group(chappaqua_source)

@watcher.on(fetch_chappaqua, when=lambda posts: has_keywords(posts))
async def notify_on_match(posts):
    send_email_alert(posts)
```

- Each Facebook group is an **independent async task** running on its own 2-minute schedule
- Sources run concurrently — two groups are polled in parallel, not sequentially
- The `when=` predicate is the keyword filter — the handler only fires when at least one post matches
- A second source (`drain_buffer`) runs in parallel to archive all posts to JSONL, regardless of keyword matches

**Key property:** CugaWatcher is purely event-driven. Between polls, each source task sleeps — no CPU usage, no busy-waiting.

---

### Layer 2: The Facebook Scraper

Facebook does not provide a public API for group posts. The scraper uses **Playwright** (headless Chromium) with a saved browser session to access groups as a logged-in user.

```
setup_session.py (run once)
  → launches real browser
  → user logs in manually (handles 2FA)
  → saves cookies + localStorage to fb_session.json

_fetch_facebook_group() (runs every 2 min)
  → launches headless Chromium with saved session
  → navigates to group URL
  → scrolls feed 3× to load more posts
  → extracts [role="article"] elements
  → parses text + permalink URL from each post
  → returns list of {text, url, source_name}
```

**Deduplication** (`state.py`): Each post is fingerprinted with SHA-1 (group URL + first 300 chars of text). Post IDs are stored in `seen_posts.json` and persist across restarts. A post is only processed once — even if the monitor is restarted between polls. The state file is capped at 5,000 entries.

---

### Layer 3: The Alert — Where CUGA Fits

For the nanny finder, the alert action is `type: "email"` — a plain Python SMTP send. This is intentional: when a nanny post is found, you want a fast, reliable, deterministic notification. No LLM involvement, no latency from an AI call.

**So where does CUGA's intelligence come in?**

CUGA's role in this use case is at the **configuration and invocation layer**, not the notification layer:

| CUGA component | Role in this use case |
|---|---|
| `CugaWatcher` | The proactive scheduler that makes the whole thing event-driven |
| `WatchExecutor` | Wires the JSON config into a running watcher — no manual wiring code |
| `WatchInstructionParser` | Lets you describe the watch in plain English and generates the JSON config |
| `watch_tools` + chat interface | Start, stop, and manage watches via natural language |

You can start this entire monitor by typing:
```
python docs/examples/facebook_monitor/chat.py "watch the Chappaqua Moms group every 2 minutes for nanny posts and email me"
```
CUGA parses that instruction, generates the config, starts the watcher, and confirms with the watch ID — no JSON editing required.

For more complex use cases (like the Gen AI RSS newsletter), CugaAgent also steps in to compose the notification content. In the nanny finder, the content is simple enough that the raw post text is exactly what you want in the email — no curation needed.

---

## Why Kafka?

In direct mode, polling and email dispatch happen in the same process. Kafka decouples them:

| Concern | Direct Mode | Kafka Mode |
|---|---|---|
| **Reliability** | If the process crashes, you miss posts during the downtime | Consumer replays messages from the topic when it restarts — no missed posts |
| **Process isolation** | Playwright (browser) and SMTP run in the same process | Browser-heavy producer runs separately from the lightweight consumer |
| **Multiple actions** | One process, one notification channel | Multiple consumers can subscribe — e.g. email consumer + SMS consumer simultaneously |
| **Observability** | Hard to inspect what was matched without adding logging | Every matched post is a Kafka message — visible, replayable, auditable |
| **Scaling** | One machine does everything | Producer can run on a VPS near the groups; consumer runs where you need it |

For a personal nanny hunt, direct mode is the simpler choice. Kafka becomes valuable if you want multiple notification channels, a remote deployment, or replay capability if your email consumer goes down.

---

## End-to-End Workflow (Direct Mode)

```
T=0:00   Monitor starts
         → Facebook session loaded from fb_session.json
         → 2 polling tasks scheduled
         → First poll runs immediately

T=0:10   Chappaqua Moms polled
         → 15 posts fetched
         → 13 already in seen_posts.json → skipped
         → 2 new posts
           · "Looking for a part-time nanny..." → keyword match ✓
           · "Anyone know a good plumber?" → no match
         → seen_posts.json updated with 2 new hashes
         → Email sent immediately:
              Subject: Watch alert: nanny in Chappaqua Moms
              Body:
              Source: Chappaqua Moms
              Keywords: nanny
              Link: https://facebook.com/groups/.../permalink/...
              Excerpt: Looking for a part-time nanny, 3 days/week,
                       starts September. Kids are 4 and 7...

T=0:12   Babysitter's Club polled
         → 8 posts fetched → all seen → no action

T=2:10   Chappaqua Moms polled again
         → 3 new posts since last check
         → None contain keywords → no email

T=2:12   Babysitter's Club polled
         → 2 new posts
           · "Need a sitter this Friday evening..." → keyword match ✓
         → Email sent immediately

... continues every 2 minutes, 24/7 ...
```

---

## Kafka Message Format

Each Kafka message published by the producer is a self-contained JSON event:

```json
{
  "event_id": "a3f2c1b0-...",
  "watch_id": "nanny-watch-01",
  "thread_id": "watch-nanny-watch-01",
  "timestamp": "2026-03-12T02:14:33Z",
  "source_name": "Chappaqua Moms",
  "source_url": "https://www.facebook.com/groups/603241227268048",
  "matches": [
    {
      "text": "Looking for a part-time nanny, 3 days/week...",
      "url": "https://facebook.com/groups/.../permalink/...",
      "source_name": "Chappaqua Moms",
      "matched_keywords": ["nanny"]
    }
  ]
}
```

The `thread_id` is stable across restarts — the consumer uses it to scope CugaAgent memory to this specific watch, so the agent remembers context from previous alerts.

---

## Configuration — One JSON File

```json
{
  "description": "Watch Facebook groups for nanny/childcare posts",
  "sources": [
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/603241227268048",
      "name": "Chappaqua Moms",
      "interval_minutes": 2,
      "extra": { "session_file": "./docs/examples/facebook_monitor/fb_session.json" }
    },
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/447824501555433",
      "name": "Babysitter's Club",
      "interval_minutes": 2,
      "extra": { "session_file": "./docs/examples/facebook_monitor/fb_session.json" }
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["nanny", "child care", "sitter"]
  },
  "actions": [{
    "type": "email",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_username": "you@gmail.com",
    "smtp_password": "<gmail-app-password>",
    "email_to": "you@gmail.com"
  }],
  "archive_enabled": true,
  "archive_file": "watch_archive.jsonl",
  "archive_interval_minutes": 2
}
```

For Kafka mode, add the `kafka` block and set `watch_id` + `thread_id` for stable identity:
```json
{
  "watch_id": "nanny-watch-01",
  "thread_id": "watch-nanny-watch-01",
  "kafka": {
    "bootstrap_servers": "localhost:9092",
    "topic": "cuga.watch.events",
    "group_id": "cuga-watch-consumers"
  }
}
```

---

## Prerequisites

### 1. Save Your Facebook Session (one-time)

Facebook requires login. The monitor uses a saved Playwright browser session so it never has to enter credentials again.

```bash
python docs/examples/facebook_monitor/setup_session.py
```

This opens a real browser window. Log in normally (including 2FA if enabled). The session is saved to `fb_session.json` and reused for all future polls.

> **Note:** Sessions expire after some weeks. Re-run `setup_session.py` if the monitor stops fetching posts.

### 2. Gmail App Password

Gmail requires an App Password (not your regular password) for SMTP access:
1. Enable 2-Step Verification on your Google account
2. Go to `myaccount.google.com → Security → App Passwords`
3. Generate a password for "Mail"
4. Use this 16-character password in the config

---

## File Structure

```
docs/examples/facebook_monitor/
├── event_monitor.py     ← CugaWatcher-based monitor (recommended)
├── monitor.py           ← Simple polling loop alternative
├── fb_tools.py          ← LangChain tools: fetch_group_posts, send_email_alert, send_sms_alert
├── setup_session.py     ← One-time Facebook login → saves fb_session.json
├── state.py             ← Deduplication: SeenPostsStore backed by seen_posts.json
├── events.py            ← Event dataclasses: NewPostEvent, KeywordMatchEvent
├── fb_session.json      ← Saved browser session (gitignored)
├── seen_posts.json      ← Dedup state — SHA-1 hashes of seen posts (gitignored)
└── architecture.md      ← This document

docs/examples/kafka/
├── watch_config_kafka.json  ← Kafka mode config for nanny finder
└── architecture.md          ← Kafka vs classic mode architecture overview

src/cuga/watch/
├── models.py           ← WatchConfig, WatchSource, WatchCondition, WatchAction
├── executor.py         ← WatchExecutor + _fetch_facebook_group (Playwright scraper)
├── tools.py            ← LangChain tools: start_watch, stop_watch, list_watches
├── kafka_models.py     ← KafkaWatchConfig (extends WatchConfig)
├── kafka_producer.py   ← KafkaWatchProducer: polls groups, publishes to topic
└── kafka_consumer.py   ← KafkaWatchConsumer: reads topic, dispatches actions

src/cuga/
└── watcher.py          ← CugaWatcher: event-driven async scheduler
```

---

## Nanny Finder vs Gen AI RSS — Side-by-Side Comparison

| | Nanny Finder | Gen AI RSS |
|---|---|---|
| **Source type** | Facebook groups (Playwright scraper) | RSS/Atom feeds (HTTP fetch) |
| **Poll interval** | Every 2 minutes — time sensitive | Every 4 hours — batch reading |
| **Keywords** | 3 specific terms: nanny, sitter, child care | 25+ broad AI/ML terms |
| **Action type** | `email` — raw post text, sent immediately | `agent_notify` — CugaAgent curates into newsletter |
| **Dispatch mode** | Immediate — one email per matching post | Batched — one newsletter per cycle across all sources |
| **CugaAgent role** | Scheduler + config layer only | Scheduler + newsletter editor (LLM call per cycle) |
| **Deduplication** | SHA-1 per post, persisted to `seen_posts.json` | Not yet implemented (future: CUGA memory) |
| **Kafka value** | Resilience + multi-channel alerts | Resilience + separation of polling from LLM dispatch |

---

## Summary

| What | How |
|---|---|
| **Problem** | Missing time-sensitive nanny posts in busy Facebook groups |
| **Solution** | 24/7 automated monitoring → instant email alert on match |
| **Scheduler** | `CugaWatcher` — concurrent async polling, one task per group |
| **Scraper** | Playwright headless Chromium with saved Facebook session |
| **Deduplication** | SHA-1 fingerprint per post, persisted across restarts |
| **Filter** | Python keyword matching — fast, zero LLM cost |
| **Alert** | Python SMTP — immediate, deterministic, no AI latency |
| **Scale-out** | Kafka — replay missed alerts, run email + SMS consumers in parallel |
| **Config** | One JSON file — add groups or keywords without touching code |
| **Invocation** | Plain English: "watch Chappaqua Moms for nanny posts and email me" |
