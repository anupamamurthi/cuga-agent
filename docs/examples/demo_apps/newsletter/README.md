# Newsletter Agent

An autonomous AI newsletter that monitors RSS feeds, curates content, and delivers
a styled HTML digest — powered by **CugaAgent**, **CugaWatcher**, and **cuga-triggers**.

---

## Quick start

```bash
# from repo root
cd docs/examples/demo_apps/newsletter

# Set your LLM provider (one of these)
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
# or RITS_API_KEY / WATSONX_APIKEY

# (optional) Set SMTP to actually send email
export SMTP_USERNAME=you@gmail.com
export SMTP_PASSWORD=your-app-password
export NEWSLETTER_TO=you@gmail.com
```

---

## Two entry points

### 1. `chat.py` — natural language interface  *(new)*

Talk to CUGA in plain English.  The utterance IS the configuration.

```bash
# One-shot: fetch and summarize now
uv run python chat.py "Fetch the latest AI news from arXiv and HuggingFace and send me a digest"

# Continuous monitor (uses CugaWatcher)
uv run python chat.py \
  "Monitor generative AI RSS feeds for significant developments, \
   CUGA/ALTK mentions, and interesting agent research across \
   https://arxiv.org/rss/cs.AI and https://huggingface.co/blog/feed.xml"

# Interactive REPL
uv run python chat.py
```

**Intent detection** (keyword-based, no extra LLM call):

| Words in utterance | Mode |
|--------------------|------|
| `monitor`, `watch`, `track`, `keep` | Continuous — CugaWatcher polls every `--interval` minutes |
| `run`, `fetch`, `send`, `now` | One-shot — agent.invoke() once |
| `stop`, `halt` | Stop active watcher |
| `status` | Show whether a monitor is running |

```bash
# Change polling interval (default 240 min)
uv run python chat.py --interval 60 "Watch arXiv cs.AI every hour for agent breakthroughs"

# Choose provider
uv run python chat.py --provider anthropic "Monitor arXiv for CUGA mentions"
uv run python chat.py --provider openai --model gpt-4o "Run the newsletter now"
```

### 2. `run.py` — scheduled / webhook mode

Pre-configured via `config.json`.  Uses `CronTrigger` + `WebhookTrigger`.

```bash
# Start daemon (scheduled + webhook listener)
uv run python run.py

# Fire once immediately (good for testing)
uv run python run.py --run-now

# Override provider
uv run python run.py --provider anthropic --run-now

# Trigger via webhook while daemon is running
curl -X POST http://127.0.0.1:8401/newsletter \
     -H 'Content-Type: application/json' \
     -d '{"message": "Send the newsletter now", "thread_id": "newsletter-ai-digest"}'
```

---

## Testing

### 1. Verify the RSS fetcher works (no LLM, no email)

```bash
uv run python -c "
from newsletter.tools import fetch_rss
import json, sys
sys.path.insert(0, 'docs/examples/demo_apps/newsletter')
sys.path.insert(0, 'docs/examples/demo_apps')
from tools import fetch_rss
items = json.loads(fetch_rss.invoke({
    'url': 'https://arxiv.org/rss/cs.AI',
    'keywords': ['agent', 'LLM', 'CUGA'],
    'max_items': 5
}))
print(f'Fetched {len(items)} items')
for it in items:
    print(' •', it['title'][:80])
"
```

### 2. One-shot run without email (prints to stdout)

If `SMTP_USERNAME` is not set, the agent prints the newsletter instead of sending it.

```bash
uv run python docs/examples/demo_apps/newsletter/chat.py \
  "Fetch the latest AI research from https://arxiv.org/rss/cs.AI and summarize the top 5 papers"
```

### 3. One-shot with `run.py`

```bash
uv run python docs/examples/demo_apps/newsletter/run.py --run-now
```

### 4. Test the continuous monitor with a short interval

Start a watcher that polls every minute instead of every 4 hours:

```bash
uv run python docs/examples/demo_apps/newsletter/chat.py \
  --interval 1 \
  "Monitor https://arxiv.org/rss/cs.AI and https://huggingface.co/blog/feed.xml \
   for CUGA, ALTK, and agent research"
```

You should see a log line within ~1 minute:
```
poll_feeds: N keyword-matched items across 2 sources
Dispatching N items to CugaAgent for curation
```

### 5. Interactive REPL

```bash
uv run python docs/examples/demo_apps/newsletter/chat.py

You: fetch the latest AI news from arXiv and HuggingFace
CUGA: Fetching and curating…
# … agent response + newsletter text …

You: monitor arXiv cs.AI for CUGA mentions every 2 hours
CUGA: Monitor started. Running in background — I'll notify you when items arrive.

You: status
CUGA: Monitoring is running.
  Request: monitor arXiv cs.AI for CUGA mentions every 2 hours

You: stop
CUGA: Monitoring stopped.
```

### 6. Webhook trigger (while `run.py` daemon is running)

```bash
# Terminal 1
uv run python docs/examples/demo_apps/newsletter/run.py

# Terminal 2
curl -X POST http://127.0.0.1:8401/newsletter \
     -H 'Content-Type: application/json' \
     -d '{"message": "Send the newsletter now", "thread_id": "newsletter-ai-digest"}'
```

---

## Configuration (`config.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `sources` | 5 feeds | RSS/Atom URLs to monitor |
| `keywords` | 30+ terms | Filter words (includes CUGA, ALTK) |
| `schedule` | `0 9 * * 1` | Cron schedule for `run.py` (Monday 9am) |
| `webhook_port` | `8401` | Port for webhook trigger in `run.py` |
| `top_items_to_select` | `12` | Items per newsletter issue |
| `llm.provider` | auto-detect | `rits \| watsonx \| openai \| anthropic \| litellm \| ollama` |

Default sources:
- arXiv cs.AI — `https://arxiv.org/rss/cs.AI`
- arXiv cs.LG — `https://arxiv.org/rss/cs.LG`
- HuggingFace Blog — `https://huggingface.co/blog/feed.xml`
- Hacker News (AI/LLM filtered)
- VentureBeat AI

---

## Email setup

The `send_email` tool reads from environment variables:

```bash
export SMTP_HOST=smtp.gmail.com      # default
export SMTP_PORT=587                  # default
export SMTP_USERNAME=you@gmail.com
export SMTP_PASSWORD=abcd-efgh-ijkl  # Gmail app password
export NEWSLETTER_TO=you@gmail.com   # comma-separated
```

For Gmail, generate an **App Password** at myaccount.google.com → Security → 2-Step → App passwords.

Without these vars, the agent prints the HTML newsletter to stdout — useful for testing.

---

## Architecture

```
chat.py (NL interface)                    run.py (scheduled)
      │                                         │
      │  "monitor..."                    CronTrigger (cron)
      │                                  WebhookTrigger (HTTP)
      ▼                                         │
 CugaWatcher  ←── @source polls RSS every N min │
      │                                         │
      │  keyword-matched items                  │  trigger message
      ▼                                         ▼
 CugaAgent ──────────────────────────── CugaAgent
  • fetch_rss (per-feed, filtered)       • fetch_rss
  • send_email                           • send_email
  • newsletter_curation skill            • newsletter_curation skill
      │                                         │
      ▼                                         ▼
  📧 HTML digest email               📧 HTML digest email
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full trigger model details.
