# Newsletter — cuga++ demo

An AI-powered RSS newsletter that monitors feeds, curates content, and delivers
digests on a schedule. Demonstrates the full cuga++ pipeline pattern:
DataChannel → buffer → TriggerChannel → Agent → OutputChannel.

```
python chat.py                              # interactive REPL
python app.py                               # browser chat at localhost:8769
python chat.py "watch arxiv, email me@co.com every 2 hours"
```

---

## Personas

There are two people involved — they have completely different jobs and never
touch each other's concerns.

### Developer

Configures what the app *can* do. Ships the app. Never interacts with it at
runtime.

Owns:
- `cuga_pipelines.yaml` — declares channel types, defaults, trigger schedule,
  output format
- `agent.py` — the newsletter writer agent (no tools, just skills)
- `chat.py` / `app.py` — the entry point; wires ChannelPlanner + CugaHost +
  ConversationGateway
- `skills/newsletter_curation.md` — the writing style guide for the agent

### End user

Opens the app. Types what they want. Done. Never sees a config file.

Interaction:
```
You:  "Watch arxiv for agent research, email me@co.com every 2 hours"
CUGA: "Monitor started. 5 feeds, digest every 120 min → me@co.com."

You:  "status"
CUGA: "Monitor active — 5 feeds, every 120 min, delivery → me@co.com."

You:  "stop"
CUGA: "Monitor stopped."
```

---

## How configuration works

There are two layers of configuration. They are independent.

### Layer 1 — Developer: `cuga_pipelines.yaml` (static defaults)

Declares the pipeline shape. Defines what happens if the user doesn't specify
something. Loaded once at startup by `CugaHost`.

```yaml
pipelines:
  - id: newsletter-digest
    factory: newsletter
    trigger:
      type: cron
      default_schedule: "0 8 * * 1-5"   # weekdays at 8am, if user doesn't specify
    data:
      - type: rss
        default_sources:
          - "https://arxiv.org/rss/cs.AI"
          - "https://huggingface.co/blog/feed.xml"
        default_keywords: ["LLM", "agent", "RAG"]
    output:
      type: email
      subject_prefix: "CUGA Newsletter"
    require_buffer: true
```

The user never touches this file. It is the developer's decision about what
sensible defaults look like for this application.

### Layer 2 — End user: natural language at runtime

The user's chat input is parsed by `ChannelPlanner` into a structured config
dict that overrides the YAML defaults. The live runtime is built from that dict.

```
"watch arxiv every hour, email me@co.com"
        ↓  ChannelPlanner
{
  "digest_minutes": 60,
  "email": "me@co.com",
  "sources": [...defaults from yaml...],   ← user didn't mention, so defaults apply
  "intent": "monitor"
}
        ↓  CugaHost.start_runtime("newsletter", config)
        ↓  builds CugaRuntime with live channels
```

The user only specifies what they care about. Everything else comes from the
developer's defaults.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  chat.py / app.py  (front door)                         │
│  User types → ChannelPlanner → PlannerResult            │
│                      ↓                                  │
│  CugaHostClient.start_runtime("newsletter", config)     │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / embedded
┌───────────────────────▼─────────────────────────────────┐
│  CugaHost  (daemon)                                     │
│  Generic — knows nothing about RSS or newsletters.      │
│  Owns CugaRuntime instances.                            │
│  Persists configs → restores on restart.                │
└───────────────────────┬─────────────────────────────────┘
                        │ builds + runs
┌───────────────────────▼─────────────────────────────────┐
│  CugaRuntime  (the actual pipeline)                     │
│  RssChannel   — polls feeds every N min → buffer        │
│  CronChannel  — fires agent on schedule                 │
│  Agent        — curates items, writes HTML newsletter   │
│  EmailChannel — delivers to recipient                   │
└─────────────────────────────────────────────────────────┘
```

Two agents, two roles, never mixed:

| Agent | Lives in | Job |
|---|---|---|
| Planning agent | `ChannelPlanner` | Parses user's chat → structured config. Runs once per message. |
| Pipeline agent | `agent.py` (`make_agent`) | Reads RSS buffer, writes newsletter HTML. Runs on cron schedule. |

---

## Data flow

```
1. User: "Watch arxiv every 2 hours, email me@co.com"

2. ChannelPlanner (internal planning agent)
   → calls configure_monitor(digest_minutes=120, email="me@co.com", ...)
   → returns PlannerResult.config

3. CugaHostClient.start_runtime("newsletter", config)

4. CugaHost builds CugaRuntime:
     RssChannel(sources=[arxiv...], poll_minutes=15)
     CronChannel(schedule="*/120 * * * *")
     EmailChannel(to="me@co.com")
     agent = make_agent()

   [15 minutes later]
5. RssChannel polls arxiv → finds 8 matching articles → pushes to buffer

   [2 hours later]
6. CronChannel fires → runtime checks buffer → 8 items present → calls agent

7. Agent receives: trigger message + buffer contents (8 RSS items)
   Applies newsletter_curation skill → writes styled HTML

8. CugaRuntime passes output to EmailChannel → email sent to me@co.com
```

Steps 5–8 repeat on every cron tick until the user types "stop".

---

## Files

| File | Owner | Purpose |
|---|---|---|
| `cuga_pipelines.yaml` | Developer | Default pipeline shape; channel types; fallback schedule |
| `agent.py` | Developer | Newsletter writer agent factory |
| `skills/newsletter_curation.md` | Developer | Writing style guide loaded by the agent |
| `chat.py` | Developer | Terminal REPL entry point |
| `app.py` | Developer | Browser chat entry point (WebSocket, port 8769) |

The end user runs one of `chat.py` or `app.py` and speaks naturally. Everything
else is invisible to them.
