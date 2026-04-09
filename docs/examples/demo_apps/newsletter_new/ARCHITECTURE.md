# Newsletter New — Architecture

## Pattern: Universal App Registration

This app demonstrates the new cuga++ architecture where **CugaHost is a universal daemon** that any app can register with. No app-specific factory modules, no `--factories` flag, no per-app host configuration.

---

## What changed from newsletter (old) to newsletter_new

| | Old | New |
|---|---|---|
| CugaHost startup | `cugahost start --factories newsletter.host_factories` | `cugahost start` |
| App code | `host_factories.py` + `PipelineBuilder` in app | None — router lives in CugaHost |
| chat.py | ~50 lines, wires PipelineBuilder + CugaREPL | ~25 lines, just registers + starts REPL |
| Routing | App-side PipelineBuilder | CugaRouter inside CugaHost |

---

## Full flow

```
cugahost start           ← one universal daemon, knows all standard channel types

python chat.py           ← on every startup:
  │
  ├── register_app("newsletter", agent="agent:make_agent", skills_dir="./skills")
  │     POST /app  →  CugaHost stores agent factory + skills dir
  │
  └── CugaREPL loop
        │
        │  You: "watch arxiv for AI agents, email me@x.com every morning"
        │
        ▼
  CugaHostClient.chat("newsletter", utterance)
        │  POST /app/newsletter/chat
        ▼
  CugaRouter (LLM classifier — lives in CugaHost)
        │
        ├── mode: PIPELINE
        │     pipeline_config: {
        │       data_type: "rss",
        │       sources: ["https://arxiv.org/rss/cs.AI"],
        │       keywords: ["AI agent"],
        │       schedule: "0 8 * * *",
        │       output_type: "email",
        │       email: "me@x.com"
        │     }
        ▼
  CugaHost._handle_pipeline()
        │  builds CugaRuntime from config dict — no factory needed
        ▼
  CugaRuntime (running in background)
        ├── RssChannel(sources=[arxiv], keywords=["AI agent"])  — polls every 15min
        ├── CronChannel("0 8 * * *")                            — fires at 8am
        └── CugaAgent → newsletter_curation skill → EmailChannel(to="me@x.com")
```

---

## Three routing modes

**DIRECT** — answer immediately, no pipeline created:
```
You: "what are the key AI trends this week?"
→ CugaRouter: mode=DIRECT
→ agent.invoke(message) → answer returned
```

**PIPELINE** — create a background runtime from NL config:
```
You: "watch arxiv hourly, send digest at midnight to me@x.com"
→ CugaRouter: mode=PIPELINE, extracts full channel config
→ CugaHost builds RssChannel + CronChannel + EmailChannel
→ "Pipeline 'arxiv-daily' is now running."
```

**CONTROL** — manage existing runtimes:
```
You: "list my pipelines"   → returns active pipeline list
You: "stop the arxiv pipeline" → runtime stopped
```

---

## App files (minimal)

```
newsletter_new/
  agent.py          — make_agent() — CugaAgent with newsletter_curation skill
  skills/
    newsletter_curation.md  — system prompt for curation
  chat.py           — 25 lines: register_app + CugaREPL
  examples.json     — test utterances with expected outputs
  ARCHITECTURE.md   — this file
```

No `host_factories.py` needed. No `channel_schemas.py`. No `pipeline_builder.py`.

---

## How to run

```bash
# Prerequisites
pip install cuga cuga-channels cuga-skills pyyaml
export ANTHROPIC_API_KEY=...
export SMTP_HOST=smtp.gmail.com   # for email delivery
export SMTP_USER=you@gmail.com
export SMTP_PASS=your-app-password

# Step 1: Start CugaHost (once, keep it running)
cugahost start

# Step 2: Run the app
cd docs/examples/demo_apps/newsletter_new
python chat.py

# One-shot mode
python chat.py "watch arxiv for AI agents, email me@example.com every morning"
```

---

## Testing

```
You: watch arxiv for AI agents, email me@example.com every morning
CUGA: Pipeline 'arxiv-ai-daily' is now running.
      Data:     rss (1 source(s))
      Schedule: 0 8 * * *
      Delivery: me@example.com

You: list my pipelines
CUGA: 1 active pipeline(s):
      • arxiv-ai-daily  (factory=__app__, running=True)

You: what are the key AI trends this week?
CUGA: [direct answer from agent]

You: stop the arxiv pipeline
CUGA: Stopped pipeline 'arxiv-ai-daily'.
```

### Force immediate trigger (for testing without waiting for cron)
```bash
curl -X POST http://127.0.0.1:18790/runtime/arxiv-ai-daily/trigger \
  -H "Content-Type: application/json" \
  -d '{"message": "Send the digest now."}'
```

### Check status
```bash
curl http://127.0.0.1:18790/app/newsletter/pipelines
curl http://127.0.0.1:18790/runtime/arxiv-ai-daily/triggers
```
