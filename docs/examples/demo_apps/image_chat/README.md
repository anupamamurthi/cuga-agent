# Screenshot Analyst

An image intelligence pipeline built with **cuga + cuga++**.

Two trigger sources. One agent. Zero manual invocation code.

---

## What this demonstrates

### The cuga++ value

cuga gives you the agent brain. cuga++ gives it ears and a delivery system.

| Capability | Core cuga | With cuga++ |
|---|---|---|
| Invoke the agent | You call `agent.invoke()` manually | Channels fire the agent automatically |
| Multiple input sources | You write a polling loop + HTTP server | Each source is one channel object |
| Add a new trigger later | Restructure the whole app | Add one more channel to the list |
| Concurrent triggers | You manage `asyncio.gather()` yourself | Framework handles concurrency |
| Output routing | You write the delivery logic | `smart_deliver()` |
| File deduplication | You track seen files yourself | CugaWatcher moves files before invoking |

### What you'd need to write without cuga++

To replicate this app without cuga++, you'd need to write ~100-150 lines of
infrastructure before touching any agent logic:

```python
# ❌ Without cuga++ — the plumbing you'd have to write

# Trigger 1: folder polling
async def poll_folder():
    seen = set()
    while True:
        for f in inbox.iterdir():
            if f not in seen and f.suffix in EXTENSIONS:
                seen.add(f)
                shutil.move(f, processed / f.name)
                result = await agent.invoke(f"Analyze: {f}")
                write_report(f.name, result.answer)
        await asyncio.sleep(15)

# Trigger 2: HTTP server
from aiohttp import web
async def handle_webhook(request):
    body = await request.json()
    result = await agent.invoke(str(body))
    return web.Response(text=result.answer)
app = web.Application()
app.router.add_post("/analyze", handle_webhook)
runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, "0.0.0.0", port)
await site.start()

# Now wire them together
await asyncio.gather(poll_folder(), asyncio.Event().wait())
# ...and add error isolation, retry logic, logging, output routing yourself
```

```python
# ✅ With cuga++ — the same two triggers

watcher = build_watcher(agent, watch_dir, report_file)   # ~20 lines
runtime = build_runtime(agent, report_file, port)         # ~15 lines

await asyncio.gather(watcher.start(), runtime.launch())   # done
```

---

## Architecture

```
                 ┌─────────────────────────────────────┐
                 │         cuga++ channels              │
                 │                                     │
  📁 Drop any   │  CugaWatcher                        │
     image into │  • polls ./inbox/ every 15s          │
     ./inbox/   │  • moves file before invoking        │
                │  • error-isolates per file            │
                │                      │               │
  🌐 POST to    │  CugaRuntime                        │
     /analyze   │  • WebhookChannel on :18791           │
     with JSON  │  • parses body, builds prompt         │
                │                      │               │
                └──────────────────────┼───────────────┘
                                       │  plain text prompt
                                       ▼
                ┌──────────────────────────────────────┐
                │  CugaAgent                           │
                │  • tool: analyze_image (docling OCR) │
                │  • skill: analyst.md  (routing +     │
                │           output format guide)       │
                │  • thread memory (LangGraph)         │
                └──────────────────────┬───────────────┘
                                       │  analysis text
                                       ▼
                ┌──────────────────────────────────────┐
                │  Output                              │
                │  • report.md  (persistent log)       │
                │  • terminal   (live print)           │
                │  • smart_deliver() on webhook path   │
                │    (email/Slack if configured)       │
                └──────────────────────────────────────┘
```

### How docling fits in

docling is the agent's extraction **tool** — it runs locally, converts images/PDFs
to clean markdown (OCR, tables, layout-aware), and returns plain text to the agent.
The LLM only ever sees text. No vision-capable model is required.

```
Image/PDF → analyze_image tool → docling → markdown → LLM reasons over text
```

---

## Quick start

### 1. Install

```bash
pip install docling
pip install -e "../../..[all]"   # cuga deps
```

### 2. Configure your LLM

```bash
export RITS_API_KEY=...           && export LLM_PROVIDER=rits
# or
export ANTHROPIC_API_KEY=...      && export LLM_PROVIDER=anthropic
# or
export OPENAI_API_KEY=...         && export LLM_PROVIDER=openai
```

### 3. Run

```bash
cd docs/examples/demo_apps/image_chat
python main.py
```

You'll see:

```
  Screenshot Analyst  —  cuga++ pipeline
  ────────────────────────────────────────────
  Drop folder  : ./inbox  (poll every 15s)
  Webhook      : http://localhost:18791/analyze
  Report       : ./report.md
  ────────────────────────────────────────────

  Two triggers, one agent.  Waiting for events...

  Drop an image:  cp ~/Desktop/screenshot.png ./inbox/
  Send a webhook:
    curl -X POST http://localhost:18791/analyze \
         -H "Content-Type: application/json" \
         -d '{"file_path": "/path/to/image.png", "question": "What is shown?"}'
```

---

## Triggering the pipeline

### Drop folder trigger

```bash
# Drop any image or PDF — analysis fires within 15 seconds
cp ~/Desktop/screenshot.png ./inbox/
cp ~/Downloads/report.pdf   ./inbox/
cp ~/Desktop/invoice.png    ./inbox/
```

The file is moved to `./inbox/processed/` before the agent runs, so it's
never processed twice even if the agent is slow or crashes.

### Webhook trigger

```bash
# Full analysis
curl -X POST http://localhost:18791/analyze \
     -H "Content-Type: application/json" \
     -d '{"file_path": "/absolute/path/to/image.png"}'

# Targeted question
curl -X POST http://localhost:18791/analyze \
     -H "Content-Type: application/json" \
     -d '{"file_path": "/tmp/error.png", "question": "What error is shown?"}'

# From a CI/CD script (one-shot with exit)
curl -s -X POST http://localhost:18791/analyze \
     -H "Content-Type: application/json" \
     -d "{\"file_path\": \"$(pwd)/build_failure.png\", \"question\": \"What failed?\"}"
```

---

## Example output

### Drop folder — error screenshot

```
════════════════════════════════════════════════════════════════
  error.png  (2026-04-05 14:32)
════════════════════════════════════════════════════════════════
**Document type:** screenshot (error)

**Summary:** A Python terminal session showing a traceback from app.py.
The error occurs during startup and prevents the service from launching.

**Key content:**
- TypeError: unsupported operand type(s) for +: 'int' and 'str' at utils.py:42
- Call stack: main() → startup() → build_message() → utils.py:42
- Python 3.12.0, working directory: /Users/anu/project

**Notable details:** Line 42 in utils.py — `count` (int) concatenated with
a string literal. Fix: use f"{count} items found" or str(count).

**TL;DR:** App crashes at startup due to int+str TypeError in utils.py:42.
```

### Webhook trigger — architecture diagram

```
════════════════════════════════════════════════════════════════
  webhook-20260405-1435  (2026-04-05 14:35)
════════════════════════════════════════════════════════════════
**Document type:** architecture diagram

**Summary:** Three-tier microservices architecture with a React frontend,
FastAPI backend, and PostgreSQL data layer. Redis is used as a cache tier
between the API and the database.

**Key content:**
- Frontend: React on port 3000, communicates with FastAPI via REST
- Backend: FastAPI + Redis cache (5-minute TTL)
- Data: PostgreSQL primary with a read replica

**Notable details:** No auth service shown — unclear how the frontend
authenticates. Read replica suggests read-heavy workload.

**TL;DR:** Standard three-tier architecture with Redis cache; auth layer missing from diagram.
```

---

## Adding a third trigger (this is the point)

Want to also analyze images sent as email attachments?  Add one channel:

```python
from cuga_channels import IMAPChannel

runtime = CugaRuntime(
    agent=agent,
    input_channels=[
        WebhookChannel(port=18791, path="/analyze"),
        IMAPChannel(                                # ← new, agent code unchanged
            host="imap.gmail.com",
            username=os.getenv("EMAIL_USER"),
            password=os.getenv("EMAIL_PASS"),
            folder="INBOX",
        ),
    ],
    ...
)
```

The agent doesn't change. The skill file doesn't change. You added a new
input modality in 5 lines.

---

## File layout

```
image_chat/
├── main.py              ← pipeline: watcher + webhook + agent wiring
├── skills/
│   └── analyst.md       ← agent routing + output format guide
├── test_image_chat.py   ← progressive test suite
├── README.md            ← this file
├── inbox/               ← drop images here (auto-created)
│   └── processed/       ← processed files moved here
└── report.md            ← analysis log (auto-created)

demo_apps/
└── _image_utils.py      ← extract_with_docling() + make_image_message()
```

---

## Configuration

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--provider` | `LLM_PROVIDER` | auto | LLM provider |
| `--model` | `LLM_MODEL` | provider default | Model name |
| `--watch` | `WATCH_DIR` | `./inbox` | Drop folder path |
| `--output` | `REPORT_FILE` | `./report.md` | Report file path |
| `--interval` | `POLL_SECONDS` | `15` | Folder poll interval |
| `--port` | `WEBHOOK_PORT` | `18791` | Webhook port |
