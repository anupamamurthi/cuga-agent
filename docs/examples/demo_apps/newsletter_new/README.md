# Newsletter

An event-driven newsletter pipeline configured entirely through natural language.
Describe what you want to monitor and when you want it delivered — the system
sets up the pipeline automatically.

**No YAML. No Python config. No pipelines to hand-edit.**

---

## Division of Responsibilities

### The App / Infrastructure

- **CugaHost** — always-on daemon that owns all running pipelines and restores
  them across restarts. Manages `RssChannel`, `CronChannel`, and `EmailChannel`.
- **CugaRouter** (inside CugaHost) — classifies each utterance as `DIRECT`,
  `PIPELINE`, or `CONTROL` using a direct LLM call (not CugaAgent)
- **chat.py** — thin CLI client: registers the app with CugaHost, starts a REPL
- **`register_app()`** — tells CugaHost where to find `make_agent()` and the skills

The infrastructure decides *when* to run, *where* to fetch data, and *how* to
deliver output. It invokes the agent only when curation is needed.

### CugaAgent

The agent is given **no tools**. It receives a batch of buffered RSS items and
writes a styled HTML newsletter digest.

| Invocation | Input | Output |
|---|---|---|
| CronChannel fires | RSS items accumulated since last run | Styled HTML digest |
| DIRECT mode question | User's question | Direct answer |

### Skills

| Skill | Purpose |
|---|---|
| `skills/newsletter_curation.md` | How to structure the digest, tone, HTML format |

---

## Quick Start

```bash
# 1. Start CugaHost (once — keep it running)
cugahost start

# 2. Run the chat client
cd docs/examples/demo_apps/newsletter_new
python chat.py

# Or one-shot
python chat.py "watch arxiv for AI agents, email me@example.com every morning"
```

---

## Example Utterances

```
"watch arxiv cs.AI for AI agent research, email me@example.com every morning at 8am"
"monitor HuggingFace and VentureBeat for LLM news, daily digest at 6pm"
"watch https://hnrss.org/newest?q=LLM+agent, email me@x.com daily"
"arxiv and hacker news, keywords: agent RAG reasoning, log to console every 4 hours"
```

### Management commands

```
list          → show all running pipelines
status        → show host health
stop          → stop a pipeline by name
quit          → exit the REPL
```

---

## How It Works

```
You type a sentence
       │
       ▼
CugaRouter (LLM classifier in CugaHost)
       │
       ├── PIPELINE → extract config → start RssChannel + CronChannel + EmailChannel
       ├── DIRECT   → agent.invoke(question) → answer returned immediately
       └── CONTROL  → list / stop pipelines
```

When a pipeline fires:
```
CronChannel triggers at scheduled time
       │
       ▼
RSS items buffered since last run
       │
       ▼
CugaAgent + newsletter_curation skill → styled HTML digest
       │
       ▼
EmailChannel delivers to recipient
```

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `ollama` |
| `LLM_MODEL` | Model name (e.g. `claude-sonnet-4-6`, `gpt-4o`) |
| `SMTP_USERNAME` | SMTP sender address |
| `SMTP_PASSWORD` | SMTP app password |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `CUGAHOST_PORT` | CugaHost port (default: `18790`) |

---

## Files

| File | Purpose |
|---|---|
| `chat.py` | CLI client — registers app with CugaHost, starts REPL |
| `agent.py` | `make_agent()` — CugaAgent with newsletter_curation skill |
| `host_factories.py` | Registers newsletter factory with CugaHost |
| `skills/newsletter_curation.md` | Agent skill — digest format and curation rules |

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component diagram.
