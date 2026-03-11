# Event-Driven Architecture with `cuga watch`

`cuga watch` is a lightweight event-driven system. You define **what to watch**, **when to trigger**, and **what to do** — CUGA handles the polling, filtering, and dispatch loop.

```
Source (poll) ──► Condition (filter) ──► Action (dispatch)
```

No LLM is involved in the loop itself. The LLM is used exactly once at startup to parse a natural-language instruction into a config.

---

## Full Setup Guide

### 1. Prerequisites

```bash
# Install CUGA
pip install cuga-agent

# For Facebook group monitoring
pip install playwright
playwright install chromium

# For email actions (already included)
# Gmail: enable 2FA + generate an App Password at
# https://myaccount.google.com/apppasswords

# For SMS actions (optional)
pip install twilio
```

---

### 2. Credentials

Set credentials via environment variables (recommended) or directly in the config JSON.

**Email (Gmail)**

```bash
export WATCH_EMAIL_USERNAME="you@gmail.com"
export WATCH_EMAIL_PASSWORD="your-app-password"   # 16-char App Password, not your Gmail password
```

**SMS (Twilio)**

```bash
export TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TWILIO_AUTH_TOKEN="your_auth_token"
```

**Facebook session** — run the interactive setup once to save your login:

```bash
python docs/examples/facebook_monitor/setup_session.py
# Opens a browser window — log in to Facebook — session saved to fb_session.json
```

---

### 3. Config file structure

A `WatchConfig` JSON has four sections:

```json
{
  "description": "human-readable summary",
  "sources":   [...],     // what to poll
  "condition": {...},     // when to trigger
  "actions":   [...],     // what to do on match
  "archive_enabled": true,
  "archive_file": "watch_archive.jsonl",
  "archive_interval_minutes": 2
}
```

---

## Examples

### Example 1 — Facebook group → email

Watch a local Facebook group for childcare posts and email yourself.

```json
{
  "description": "Watch Chappaqua Moms for nanny or sitter posts",
  "sources": [
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/603241227268048",
      "name": "Chappaqua Moms",
      "interval_minutes": 30,
      "extra": {
        "session_file": "./docs/examples/facebook_monitor/fb_session.json",
        "limit": 20
      }
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["nanny", "child care", "sitter", "babysitter", "au pair"]
  },
  "actions": [
    {
      "type": "email",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "smtp_username": "",
      "smtp_password": "",
      "email_to": "you@gmail.com",
      "email_from": ""
    }
  ],
  "archive_enabled": true,
  "archive_file": "watch_archive.jsonl",
  "archive_interval_minutes": 2
}
```

```bash
uv run cuga watch --config watch_config.json
```

---

### Example 2 — Multiple Facebook groups → email

Same keywords, multiple groups, each on its own poll schedule. The same session file works across groups.

```json
{
  "description": "Watch multiple local groups for nanny posts",
  "sources": [
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/603241227268048",
      "name": "Chappaqua Moms",
      "interval_minutes": 30,
      "extra": { "session_file": "./fb_session.json" }
    },
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/ANOTHER_GROUP_ID",
      "name": "Westchester Parents",
      "interval_minutes": 20,
      "extra": { "session_file": "./fb_session.json" }
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["nanny", "sitter", "child care"]
  },
  "actions": [
    {
      "type": "email",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "email_to": "you@gmail.com"
    }
  ],
  "archive_enabled": true,
  "archive_file": "watch_archive.jsonl",
  "archive_interval_minutes": 2
}
```

---

### Example 3 — RSS feed → email + SMS

Watch Hacker News for AI/LLM posts and get both an email and a text.

```json
{
  "description": "Watch HN RSS for AI and LLM mentions",
  "sources": [
    {
      "type": "rss_feed",
      "url": "https://news.ycombinator.com/rss",
      "name": "Hacker News",
      "interval_minutes": 15,
      "extra": { "limit": 30 }
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["llm", "ai", "gpt", "claude", "gemini"]
  },
  "actions": [
    {
      "type": "email",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "email_to": "you@gmail.com"
    },
    {
      "type": "sms",
      "twilio_account_sid": "",
      "twilio_auth_token": "",
      "sms_from": "+15550000000",
      "sms_to": "+15551234567"
    }
  ],
  "archive_enabled": true,
  "archive_file": "hn_archive.jsonl",
  "archive_interval_minutes": 5
}
```

---

### Example 4 — Web page → log (no credentials needed)

Good for testing. Polls any URL and logs matches to stdout.

```json
{
  "description": "Watch a web page for outage mentions",
  "sources": [
    {
      "type": "web_page",
      "url": "https://status.example.com",
      "name": "Example Status Page",
      "interval_minutes": 5
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["outage", "degraded", "incident", "down"]
  },
  "actions": [
    {
      "type": "log"
    }
  ],
  "archive_enabled": false,
  "archive_file": "",
  "archive_interval_minutes": 0
}
```

---

### Example 5 — Mixed sources → email

Watch a Facebook group and an RSS feed together. Any match from either source triggers the same email action.

```json
{
  "description": "Watch FB group and local news RSS for housing posts",
  "sources": [
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/YOUR_GROUP_ID",
      "name": "Local Community Group",
      "interval_minutes": 30,
      "extra": { "session_file": "./fb_session.json" }
    },
    {
      "type": "rss_feed",
      "url": "https://www.lohud.com/arcio/rss/",
      "name": "Local News RSS",
      "interval_minutes": 10
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["rental", "for rent", "apartment", "sublet"]
  },
  "actions": [
    {
      "type": "email",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "email_to": "you@gmail.com"
    }
  ],
  "archive_enabled": true,
  "archive_file": "housing_archive.jsonl",
  "archive_interval_minutes": 2
}
```

---

### Example 6 — Natural language (no config file)

You can skip the JSON entirely. CUGA parses the instruction with an LLM call at startup:

```bash
uv run cuga watch \
  "Watch https://www.facebook.com/groups/603241227268048 for nanny or sitter and email anupama.murthi@gmail.com"
```

Dry-run to preview the parsed config before starting:

```bash
uv run cuga watch --dry-run \
  "Watch https://news.ycombinator.com/rss for AI or LLM every 10 minutes and log matches"
```

---

## Condition types

| Type | Behaviour | Config |
|------|-----------|--------|
| `keyword` | Triggers when any keyword appears in item text (case-insensitive) | `"keywords": ["nanny", "sitter"]` |
| `always` | Triggers on every non-empty poll result | `"type": "always"` |
| `custom` | Evaluates a Python expression against `items` | `"expression": "any(len(i['text']) > 500 for i in items)"` |

---

## Action types

| Type | What it does | Required credentials |
|------|-------------|----------------------|
| `log` | Prints match to stdout | None |
| `email` | Sends SMTP email | `WATCH_EMAIL_USERNAME` + `WATCH_EMAIL_PASSWORD` |
| `sms` | Sends SMS via Twilio | `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` |
| `agent_notify` | Asks CugaAgent (LLM) to compose and send a message | CUGA LLM config |

---

## Source types

| Type | What it polls | Extra options |
|------|--------------|---------------|
| `facebook_group` | Facebook group feed via Playwright | `session_file`, `limit` |
| `web_page` | Any public URL (HTML) | — |
| `rss_feed` | RSS 2.0 or Atom feed | `limit` |
| `custom` | (extend in `executor.py`) | — |

---

## Running in production

```bash
# Foreground (Ctrl+C to stop)
uv run cuga watch --config watch_config.json

# Background with nohup
nohup uv run cuga watch --config watch_config.json > watch.log 2>&1 &

# Or use a simple systemd service / launchd plist (macOS)
```

The archive file (`watch_archive.jsonl`) accumulates every item seen, regardless of whether it matched — useful for replay or auditing.
