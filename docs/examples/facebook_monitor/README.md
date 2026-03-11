# Facebook Group Monitor — CUGA Sub-Agent

A standalone sub-agent that watches Facebook groups for keywords and sends you email or SMS notifications. Built on top of the CUGA SDK — no changes to CUGA core.

> **Note:** Facebook has no public API for reading group posts. This sub-agent uses Playwright browser automation with your own Facebook session, which is against Facebook's Terms of Service. Use it for personal/research purposes and with that in mind.

## How it works

Two implementations are included — pick the one that fits your needs:

### Option A — CugaWatcher (`event_monitor.py`) — recommended

Uses `CugaWatcher` from the CUGA SDK. Two sources run on independent schedules; CUGA is only invoked when a keyword match is found.

```
Source A: fetch_posts   (every 30 min)  — scrapes Facebook, fills in-memory buffer
    ↓  dispatched to:
    notify handler  (when: keyword match found)  →  CugaAgent sends email/SMS

Source B: drain_buffer  (every 2 min)   — drains the in-memory buffer
    ↓  dispatched to:
    serialize handler  (always)  →  appends posts to posts_archive.jsonl
```

- **No wasted LLM calls** — CUGA only runs when a keyword match is found
- **Deduplication** — posts already seen in previous runs are skipped (`seen_posts.json`)
- **Post archiving** — every 2 minutes, all newly fetched posts are flushed to `posts_archive.jsonl`
- **Reusable pattern** — `CugaWatcher` is a first-class SDK class; same pattern works for any source (Slack, APIs, databases)

### Option B — Simple polling loop (`monitor.py`)

A single loop that calls CUGA on every cycle to fetch + scan + notify. Simpler but calls the LLM regardless of whether new posts exist.

```
[Scheduler loop every N minutes]
        ↓
[CUGA Agent — fetch posts → scan for keywords → notify if matches found]
```

---

## Architecture & CUGA's role

### What CUGA is (and is not) in this system

CUGA is **not** the scheduler, scraper, or keyword filter. Those are handled in pure Python before CUGA is ever called. CUGA's role is narrow and specific: it is the **reasoning and action layer** that runs only when something meaningful has already been detected.

| Layer | What does the work | When it runs |
|---|---|---|
| **Scheduling** | `CugaWatcher` (asyncio tasks) | Continuously, on a timer |
| **Scraping** | Playwright via `fb_tools._scrape_group_sync` | Every 30 min |
| **Deduplication** | `SeenPostsStore` (SHA-1 + JSON file) | On every scraped post |
| **Keyword matching** | Plain Python string search | On every new post |
| **Archiving** | `json.dumps` to `.jsonl` | Every 2 min |
| **Reasoning + notification** | **CugaAgent (LLM)** | Only when a keyword match is found |

CUGA receives a natural-language task like:

> *"Keyword 'nanny' was found in 'Chappaqua Moms'. Post excerpt: '...'. Send a consolidated email notification."*

It then uses its internal graph (task decomposition → tool selection → code execution) to call the right tool (`send_email_alert`) with a well-composed message. You could hardcode this logic yourself — the value of using CUGA here is that it reasons about *how* to compose the notification, handles multiple matches across groups, and can be instructed in plain English.

---

### Full system architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  event_monitor.py  (your process)                                       │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  CugaWatcher  (src/cuga/watcher.py)                             │   │
│  │                                                                 │   │
│  │  ┌──────────────────────┐   ┌──────────────────────┐           │   │
│  │  │  Source A            │   │  Source B             │           │   │
│  │  │  fetch_posts         │   │  drain_buffer         │           │   │
│  │  │  every 30 min        │   │  every 2 min          │           │   │
│  │  └──────────┬───────────┘   └──────────┬────────────┘           │   │
│  │             │ list[post]               │ list[post]              │   │
│  │             ▼                          ▼                         │   │
│  │  ┌──────────────────────┐   ┌──────────────────────┐           │   │
│  │  │  Handler: notify     │   │  Handler: serialize   │           │   │
│  │  │  when: keyword match │   │  when: always         │           │   │
│  │  └──────────┬───────────┘   └──────────┬────────────┘           │   │
│  │             │                          │                         │   │
│  └─────────────┼──────────────────────────┼─────────────────────────┘  │
│                │                          │                             │
│                │ matched posts            │ all new posts               │
│                ▼                          ▼                             │
│  ┌─────────────────────────┐   ┌──────────────────────────┐           │
│  │  CugaAgent              │   │  posts_archive.jsonl      │           │
│  │  (src/cuga/sdk.py)      │   │  (append, one line/post)  │           │
│  │                         │   └──────────────────────────┘           │
│  │  ┌─────────────────┐    │                                           │
│  │  │ Task decomposer │    │  ← natural-language task prompt           │
│  │  └────────┬────────┘    │    "Keyword 'nanny' found in              │
│  │           ▼             │     Chappaqua Moms. Notify me."           │
│  │  ┌─────────────────┐    │                                           │
│  │  │ Plan controller │    │                                           │
│  │  └────────┬────────┘    │                                           │
│  │           ▼             │                                           │
│  │  ┌─────────────────┐    │                                           │
│  │  │  CugaLite node  │    │  ← selects and calls the right tool       │
│  │  │  (code agent)   │    │                                           │
│  │  └────────┬────────┘    │                                           │
│  └───────────┼─────────────┘                                           │
│              │                                                          │
└──────────────┼──────────────────────────────────────────────────────────┘
               │ tool call
               ▼
┌──────────────────────────────────────────────────────────┐
│  fb_tools.py  (LangChain tools registered with the agent) │
│                                                           │
│  ┌─────────────────────┐   ┌──────────────────────────┐  │
│  │  send_email_alert   │   │  send_sms_alert           │  │
│  │  (SMTP / Gmail)     │   │  (Twilio REST API)        │  │
│  └─────────────────────┘   └──────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
               │
               ▼
        📧 Email / 📱 SMS delivered to you
```

---

### Data flow for a single keyword match

```
Facebook group page
      │  (Playwright, headless Chromium, your session cookie)
      ▼
raw post text
      │  make_post_id()  →  SHA-1 hash
      ▼
SeenPostsStore.is_new()?
      │  YES → mark_seen(), add to _post_buffer
      │  NO  → skip (already processed in a previous run)
      ▼
CugaWatcher dispatches to notify handler
      │  when: any post contains "nanny" / "child care" / "sitter"
      ▼
CugaAgent.invoke(natural-language task)
      │
      ├── Task decomposer breaks down: "compose alert + call send_email_alert"
      ├── Plan controller routes to CugaLite (fast code path)
      ├── CugaLite generates Python: send_email_alert(subject=..., body=...)
      └── Tool executes → SMTP sends email to anupama.murthi@gmail.com
```

---

### Why use CUGA instead of just calling `send_email_alert` directly?

You could skip CUGA and call the email tool directly after a keyword match. CUGA adds value when:

- **Multiple matches** across groups need to be consolidated into one coherent message
- **The notification content requires judgment** — e.g. "summarise the 3 most relevant posts, not all 12"
- **You want to change behaviour in plain English** — e.g. add a Policy playbook: *"If the word 'urgent' appears, call me instead of emailing"* — no code change required
- **You add more tools later** (calendar booking, CRM lookup) — CUGA will reason about which ones to use without you wiring them manually

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- An LLM API key (WatsonX, OpenAI, Azure, Groq, or OpenRouter — see step 2)
- A Gmail account with an App Password (or Twilio for SMS)

---

## Full Setup (start here)

### Step 1 — Clone the repo and create the virtual environment

```bash
git clone https://github.com/cuga-project/cuga-agent.git
cd cuga-agent

uv venv --python=3.12 && source .venv/bin/activate
uv sync
```

### Step 2 — Configure your LLM credentials

Create a `.env` file in the repo root:

```bash
cp .env.example .env   # if it exists, or create from scratch
```

Edit `.env` and fill in your LLM provider. Pick **one**:

**WatsonX:**
```env
AGENT_SETTING_CONFIG="settings.watsonx.toml"
WATSONX_API_KEY=your-watsonx-api-key
WATSONX_PROJECT_ID=your-project-id
WATSONX_URL=https://us-south.ml.cloud.ibm.com
```

**OpenAI:**
```env
AGENT_SETTING_CONFIG="settings.openai.toml"
OPENAI_API_KEY=sk-...your-key...
```

**Azure OpenAI:**
```env
AGENT_SETTING_CONFIG="settings.azure.toml"
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
OPENAI_API_VERSION=2024-02-01
```

**Groq:**
```env
AGENT_SETTING_CONFIG="settings.groq.toml"
GROQ_API_KEY=your-groq-api-key
```

### Step 3 — Install the Facebook monitor's extra dependencies

```bash
# Still in the repo root with the venv active
pip install playwright pyyaml
playwright install chromium

# Optional: enable SMS notifications via Twilio
pip install twilio
```

### Step 4 — Log in to Facebook (one-time)

```bash
cd docs/examples/facebook_monitor
python setup_session.py
```

A browser window opens. Log in to Facebook normally (including 2FA if you have it). Once you land on the home feed, the session is saved to `fb_session.json` and the browser closes automatically. You only need to do this once — the session is reused on every subsequent check.

> If you ever get scraping errors after a few days, Facebook may have expired your session. Just re-run `setup_session.py`.

### Step 5 — Configure the monitor

Edit `config.yaml` in this directory:

```yaml
# Facebook groups to watch
groups:
  - url: "https://www.facebook.com/groups/YOUR_GROUP_ID"
    name: "My Group"

# Keywords to alert on (case-insensitive)
keywords:
  - "for sale"
  - "hiring"

# How often to check (minutes)
check_interval_minutes: 30

# How many posts to fetch per group per check
posts_per_group: 20

notifications:
  email:
    enabled: true
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    username: "you@gmail.com"
    password: "your-app-password"   # Gmail App Password — see note below
    to: "you@gmail.com"
  sms:
    enabled: false   # set to true and fill in Twilio credentials to enable
    twilio_account_sid: "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    twilio_auth_token: "your_auth_token"
    from_number: "+15005550006"
    to_number: "+1xxxxxxxxxx"
```

**Gmail App Password:** Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create an app password for "Mail", and use that as the `password` value — not your Gmail login password. (Requires 2FA to be enabled on your Google account.)

**Finding your group URL:** Navigate to the Facebook group in your browser. The URL will look like `https://www.facebook.com/groups/123456789` — copy it directly.

### Step 6 — Start CUGA (required — runs in a separate terminal)

The monitor uses CUGA's agent core, which needs the CUGA server running. Open a new terminal:

```bash
cd cuga-agent
source .venv/bin/activate
cuga start demo
```

Wait until you see the server is ready (Chrome will open at `https://localhost:7860`). You can minimise that browser window — the monitor runs independently of the UI.

### Step 7 — Run the monitor

Back in your first terminal (still in `docs/examples/facebook_monitor`).

**Option A — CugaWatcher (recommended):**

```bash
python event_monitor.py
```

On startup you'll see:
- **Source A** (`fetch_posts`) runs immediately — scrapes your groups, deduplicates against `seen_posts.json`, buffers new posts
- **Source B** (`drain_buffer`) starts its 2-minute clock — will serialize the buffer to `posts_archive.jsonl` once posts arrive
- If any fetched post contains a keyword, **CugaAgent is invoked** to compose and send your email/SMS notification
- If no keywords match, CUGA is never called — the cycle costs zero LLM tokens

**Option B — Simple polling loop:**

```bash
python monitor.py
```

CUGA handles the full fetch → scan → notify cycle on every interval.

---

**Running in the background** (leave it running after you close your terminal):

```bash
# With screen
screen -S fb-monitor
python event_monitor.py
# Detach: Ctrl+A then D
# Reattach later: screen -r fb-monitor

# With tmux
tmux new -s fb-monitor
python event_monitor.py
# Detach: Ctrl+B then D
# Reattach later: tmux attach -t fb-monitor
```

---

## File structure

```
facebook_monitor/
├── config.yaml             # Your groups, keywords, interval, notification settings
├── setup_session.py        # One-time Facebook login — saves browser session
├── fb_tools.py             # LangChain tools: fetch_group_posts, send_email_alert, send_sms_alert
├── events.py               # Event dataclasses: NewPostEvent, KeywordMatchEvent
├── state.py                # Deduplication: tracks seen post IDs across restarts
├── event_monitor.py        # CugaWatcher-based monitor (recommended)
├── monitor.py              # Simple polling loop (alternative)
├── fb_session.json         # Created by setup_session.py (do not commit)
├── seen_posts.json         # Deduplication state, persisted across restarts (do not commit)
├── posts_archive.jsonl     # Running archive of all fetched posts, flushed every 2 min
└── README.md

# CUGA SDK additions (in repo root src/cuga/)
src/cuga/watcher.py         # CugaWatcher — new SDK class
src/cuga/__init__.py        # now exports CugaWatcher alongside CugaAgent
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Session file not found` | Run `setup_session.py` first |
| Scraping returns empty posts | Facebook may have changed its DOM. Try re-running `setup_session.py` to refresh your session, or increase `wait_for_timeout` in `fb_tools.py` |
| `Email failed: Authentication failed` | Use a Gmail App Password, not your regular Gmail password |
| `SMS failed` | Double-check Twilio credentials and confirm the number is SMS-capable |
| `OPENAI_API_KEY must be set` | You haven't set `AGENT_SETTING_CONFIG` in your `.env` — see Step 2 |
| CUGA not ready / connection refused | Make sure `cuga start demo` is running in a separate terminal (Step 6) |
| Facebook session expired | Re-run `setup_session.py` to get a fresh session |
| Monitor keeps notifying on old posts | Delete `seen_posts.json` to reset deduplication state, or just let it run — it won't re-notify once a post is marked seen |
| `posts_archive.jsonl` not created | It's created on the first drain (2 min after the first fetch). If it never appears, check that `event_monitor.py` is running and the fetch source emitted data |
| `ImportError: cannot import name 'CugaWatcher'` | Make sure you're on the latest code — `CugaWatcher` is in `src/cuga/watcher.py` and exported from `src/cuga/__init__.py` |
