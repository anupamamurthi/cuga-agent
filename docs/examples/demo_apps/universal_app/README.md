# Universal Agent — cuga++ demo

A single chat window that can create, manage, and run any cuga++ pipeline from
natural language. The user describes what they want and the agent figures out
which channels to wire, which tools to give the pipeline, what schedule to use,
and where to deliver the output.

```
python app.py
python app.py --provider anthropic
open http://127.0.0.1:8770
```

---

## What this is

Every other demo app has a fixed, pre-wired pipeline: the newsletter app monitors
RSS; the server monitor polls metrics; the smart todo watches a database. Each app
requires a developer to write an `agent.py`, a `cuga_pipelines.yaml`, and a
`host_factories.py` before a user can do anything.

The Universal Agent inverts this. There is no YAML, no pre-wired factory, no
app-specific agent. The user's chat message IS the configuration — the system
builds the pipeline on the fly.

---

## Two architectures compared

### Traditional cuga++ app (e.g. newsletter)

```
Developer writes:
  cuga_pipelines.yaml    ← static pipeline shape
  host_factories.py      ← factory: RssChannel + CronChannel + EmailChannel
  agent.py               ← agent with domain-specific tools and skills
  skills/curation.md     ← writing style guide

User runs:
  python app.py
  "watch arxiv, email me daily"  ← configures the ONE pre-wired pipeline
```

The developer owns the pipeline shape. The user only adjusts its parameters
(schedule, email address, keywords). The app can only do what the developer
pre-wired.

### Universal Agent (this app)

```
Developer writes:
  registry.py            ← manifest of all available channels and tools
  factory.py             ← generic runtime builder from a spec dict
  planner_tools.py       ← create/update/stop/list tools for the planner agent
  skills/planner.md      ← how the planner interprets user requests

User runs:
  python app.py
  "watch arxiv for AI papers, email me@co.com every morning"
  "check Bitcoin prices every hour and log them"
  "summarise new PDFs in ~/Downloads and send to Slack daily"
  ← creates completely different pipelines from scratch
```

The user owns the pipeline shape. The developer ships a toolkit (channels, tools,
a planner skill) not a fixed application. Any combination of channels and tools
the framework supports can be assembled at runtime.

---

## Architecture

```
Browser (port 8770)
    ↓ WebSocket
ConversationGateway
    ↓
CugaAgent  (planner)
  skills/planner.md  ← knows about all channels, tools, cron syntax
  ├── create_pipeline(description, data_type, trigger_schedule, output_type, ...)
  │     ├── build_factory(spec)          ← factory.py
  │     ├── host.register_factory(id, factory)
  │     └── client.start_runtime(id, id, spec)
  ├── update_pipeline(id, ...)           → client.update_runtime(...)
  ├── stop_pipeline(id)                  → client.stop_runtime(...)
  └── list_pipelines()                  → client.list_runtimes()

CugaHost (embedded)
  └── dynamic factories (one per user-created pipeline)
      └── CugaRuntime
          ├── DataChannel  (rss | imap | slack | docling | audio | ...)
          ├── CronChannel  (schedule from spec)
          ├── CugaAgent    (pipeline agent — tools from spec)
          └── OutputChannel (email | slack | telegram | discord | sms | log)
```

There are two agents:

| Agent | Lives in | Job |
|---|---|---|
| Planner agent | `app.py` / `planner_tools.py` | Interprets user chat → creates/manages pipelines |
| Pipeline agent | built by `factory.py` | Runs on a schedule → does the actual task |

The planner agent runs once per user message. The pipeline agent runs on its cron
schedule, independently, indefinitely.

---

## All supported use cases

### RSS pipelines (data_type=rss)

```
"Monitor arxiv for AI papers, email me@co.com every morning"
"Watch HackerNews for Rust posts, Slack digest to #tech every 4 hours"
"Follow HuggingFace blog and VentureBeat AI, weekly digest, email me@co.com"
"Monitor arxiv cs.LG for 'reinforcement learning', daily log"
```

**How it works**: `RssChannel` polls feeds every 15 min into a buffer. `CronChannel`
fires the pipeline agent on schedule. Agent curates the buffered items and produces
a digest. Delivered via the configured output channel.

### Tool-driven pipelines (data_type=none)

The agent fetches its own data using tools. No DataChannel. Trigger always fires.

#### Market data (tool: market_data)
```
"Check Bitcoin and Ethereum prices every hour, log the result"
"Daily stock summary for AAPL, MSFT, NVDA — email me@co.com at 9am"
"Bitcoin price alert every 30 minutes"
```

#### Web search (tool: web_search)
```
"Search for LangChain release notes every Monday, log a summary"
"Weekly AI news digest from the web, email me@co.com"
"Monitor 'Claude Anthropic' mentions daily and log them"
```

#### GitHub (tool: github)
```
"Weekly PR digest for anthropics/claude-code, email me@co.com"
"Daily open issues summary for myorg/myrepo, Slack to #dev"
"Check CI/CD workflow runs for myrepo every hour, log failures"
```

#### Google Calendar (tool: calendar)
```
"Email me my calendar events for the day, every morning at 8am"
"Weekly calendar summary every Monday at 9am"
"Remind me of back-to-back meetings every day at 7:30am"
```

#### Shell / system (tool: shell)
```
"Check disk usage every hour and log warnings"
"Daily pip dependency check for this project"
```

### Document pipelines (data_type=docling)

```
"Watch ~/Downloads for new PDFs and summarise them every 10 minutes, log output"
"Process documents in ./inbox, email summaries to me@co.com daily"
"Watch ./contracts for Word files, Slack a one-line summary to #legal"
```

**How it works**: `DoclingChannel` watches a folder, extracts PDF/image/Office
content with docling into clean markdown. Agent receives the extracted text and
produces a summary. Processed files are moved to `./watched/processed/`.

### Audio pipelines (data_type=audio)

```
"Transcribe voice memos in ./recordings every 30 minutes, log the text"
"Process meeting recordings in ./meetings, email transcripts daily"
```

**How it works**: `AudioChannel` watches a folder, transcribes audio with Whisper.
Agent receives the transcripts and can clean them up, extract action items, etc.

### Email inbox pipelines (data_type=imap)

```
"Watch my Gmail inbox every 10 minutes, summarise new emails to Slack"
"Monitor support@co.com for new tickets, log a digest every hour"
```

**Requires**: `IMAP_PASSWORD` env var + `imap_host` and `imap_username` params.

### Slack monitor pipelines (data_type=slack_data)

```
"Monitor #general every 2 hours and email a digest"
"Summarise the #support Slack channel daily"
```

**Requires**: `SLACK_BOT_TOKEN` env var.

### Pipeline management

```
"list my pipelines"
"show what's running"
"stop the arxiv pipeline"
"stop all"
"update the bitcoin pipeline to run every 30 minutes"
"change the arxiv digest to go to Slack instead of email"
```

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Entry point — ConversationGateway + embedded CugaHost + planner agent |
| `registry.py` | Manifest of all available channels and tools |
| `factory.py` | `build_factory(spec)` → dynamic CugaRuntime factory callable |
| `planner_tools.py` | `create_pipeline`, `update_pipeline`, `stop_pipeline`, `list_pipelines` |
| `skills/planner.md` | Planner agent skill: channel selection rules, cron conversion, output routing |
| `skills/pipeline_agent.md` | Generic skill for dynamically built pipeline agents |
| `tests/test_universal.py` | Full test suite |

---

## Configuration

All via environment variables. No YAML to edit.

| Variable | Required for |
|---|---|
| `LLM_PROVIDER` / `LLM_MODEL` | LLM selection (auto-detected from API keys) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | Email output |
| `SLACK_BOT_TOKEN` / `SLACK_WEBHOOK_URL` | Slack input or output |
| `TELEGRAM_BOT_TOKEN` | Telegram input or output |
| `DISCORD_BOT_TOKEN` / `DISCORD_WEBHOOK_URL` | Discord input or output |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM` | SMS output |
| `IMAP_PASSWORD` | IMAP email input |
| `TAVILY_API_KEY` | web_search tool |
| `GOOGLE_CALENDAR_ACCESS_TOKEN` | calendar tool |
| `GITHUB_TOKEN` | github tool |
| `ALPHA_VANTAGE_API_KEY` | market_data tool (stocks only; crypto is free) |

If no output credentials are configured, output goes to console via `LogChannel`.

---

## Running the tests

```bash
cd docs/examples/demo_apps/universal_app
pip install pytest pytest-asyncio
pytest tests/test_universal.py -v
```

Tests are fully mocked — no LLM, no CugaHost process, no network calls required.

---

## What this is not

The Universal Agent is a **general-purpose pipeline builder**, not an omniscient
agent. It works within the set of channels and tools in the registry:

- It cannot invent new channel types (e.g. there is no YouTube DataChannel unless
  you add one to `registry.py` and `factory.py`).
- It cannot dynamically write new Python code or install packages.
- Each pipeline is independent — pipelines do not share state or data with each other.
- The pipeline agent for a given pipeline uses only the tools specified at creation
  time.

To extend the Universal Agent with a new capability, add an entry to `registry.py`
and a corresponding branch in `factory.py`. The planner will automatically know
about the new channel or tool because its skill prompt is generated from the
registry at startup.
