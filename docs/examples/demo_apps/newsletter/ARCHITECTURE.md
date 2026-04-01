# Newsletter Agent — Architecture

## Overview

An autonomous, event-driven newsletter agent. On a cron schedule or a webhook POST, CugaAgent fetches RSS feeds, applies editorial judgment (filter, deduplicate, rank, select top N), composes a styled HTML newsletter, and delivers it via SMTP — no human in the loop.

---

## The cuga-triggers model: Watch → Emit → React

Every trigger in cuga++ follows this contract:

```
Trigger.start(invoke_fn)
    │
    │  [watches for its event: time tick, HTTP request, DB state, file change, ...]
    │
    │  when event detected:
    │    emit TriggerEvent(source, message, thread_id)
    │    invoke_fn(event)
    │         │
    │         ▼
    │    TriggerRuntime._dispatch(event)
    │         │
    │         ▼
    │    agent.ainvoke(message, thread_id)
    │         │
    │         ▼
    │    LangGraph ReAct loop → tools → LLM → answer
    │
    │  trigger's responsibility: detect the event, emit a clear message
    │  agent's responsibility:   decide what to do and do it
```

**When does the trigger do Python-level filtering vs. passing to the LLM?**

The newsletter uses two trigger types, and neither pre-filters content — both simply signal "it is time to run":

- `CronTrigger` — watches time. Fires on schedule with a rich, parameterised message (sources, keywords, top-N from config). The agent fetches, filters, ranks, and composes using its LLM and tools.
- `WebhookTrigger` — watches for an HTTP POST. The caller provides the message directly in the request body; the agent reacts however the message instructs.

This is intentional: the newsletter pipeline requires LLM judgment throughout (semantic relevance filtering, cross-issue deduplication, HTML composition). There is no binary "did the event happen?" check that belongs in Python — the event is simply the arrival of a time tick or HTTP request.

Contrast this with `smart_todo`'s `ReminderTrigger`, which *does* do Python-level filtering (querying the DB for due items) before invoking the agent — because the existence of a due reminder is a deterministic fact, not a judgment call.

---

## Architecture diagram

```
                  ┌──────────────────────────────────────────┐
                  │            CronTrigger                    │
                  │  schedule from config.json                │
                  │  (default: "0 9 * * 1", Mon 9am)         │
                  │                                           │
                  │  watches: wall clock                      │
                  │  emits:   full run message with sources,  │
                  │           keywords, top-N from config     │
                  └────────────────┬─────────────────────────┘
                                   │
                  ┌────────────────┴─────────────────────────┐
                  │           WebhookTrigger                  │
                  │  POST http://localhost:{port}/newsletter  │
                  │                                           │
                  │  watches: HTTP POST                       │
                  │  emits:   caller-supplied message +       │
                  │           optional thread_id              │
                  └────────────────┬─────────────────────────┘
                                   │
                                   │  TriggerRuntime dispatches
                                   │  invoke_fn(event)
                                   ▼
  ┌────────────────────────────────────────────────────────────────┐
  │                          CugaAgent                              │
  │                                                                  │
  │  System prompt  ◄── CugaSkillsPlugin(skills/newsletter_curation.md)
  │                       dedup rules, selection criteria,          │
  │                       HTML template, source attribution         │
  │                                                                  │
  │  LLM ←── _llm.py (RITS / WatsonX / OpenAI / Anthropic / ...)  │
  │   │                                                              │
  │   ├─► fetch_rss(url, keywords, max_items)  ──► RSS/Atom feeds   │
  │   │       ↳ repeat for each source in config                    │
  │   │                                                              │
  │   │   [LLM applies: semantic filter, dedup vs prior issues,     │
  │   │    ranking, top-N selection, HTML composition]               │
  │   │                                                              │
  │   └─► send_email(subject, html_body)  ──► SMTP                  │
  └────────────────────────────────────────────────────────────────┘
              │
              │  result + full conversation stored in checkpointer
              ▼
     per-thread_id checkpoint
     (dedup reference for next run:
      "newsletter-ai-digest" thread accumulates
      seen article URLs across issues)
```

---

## Why CronTrigger is the right fit here

`CronTrigger` fires a fixed message on schedule. Unlike `smart_todo`'s `ReminderTrigger`, it does not inspect any data source before deciding to invoke the agent — it always fires. This is appropriate because:

- There are always RSS items to fetch (the feed is never "empty in the meaningful sense")
- The hard work is editorial: semantic relevance judgment, deduplication against prior issues, quality ranking — none of which Python can do without the LLM
- The fixed message already encodes all the agent needs: source URLs, keyword themes, top-N count, all from `config.json`

If you wanted a conditional variant — e.g. "only send if ≥ 5 genuinely new items were found" — the right approach is a custom `NewsletterTrigger` that calls `fetch_rss()` in Python first, counts novel items, and only invokes the agent if the threshold is met. The `CugaTrigger` protocol supports this; it would look like `ReminderTrigger`.

---

## cuga++ package roles

| Package | Role in this app |
|---------|-----------------|
| `cuga` (CugaAgent) | Runs the LangGraph ReAct loop. Accepts `triggers=[CronTrigger, WebhookTrigger]`, starts `TriggerRuntime` internally. Checkpointing accumulates article history per `thread_id` across newsletter issues — the LLM can reference it for deduplication. |
| `cuga-skills` | `CugaSkillsPlugin` injects `skills/newsletter_curation.md` — editorial rules, HTML formatting, dedup instructions, source attribution format. |
| `cuga-triggers` | `CronTrigger` (scheduled runs) + `WebhookTrigger` (on-demand). Both registered on the same agent instance and managed by `TriggerRuntime`. |
| `cuga-runtime` | `RITSChatModel` in `_llm.py` — custom IBM RITS auth header. |

---

## Files

| File | Role |
|------|------|
| `run.py` | CLI entrypoint. `build_agent()` constructs CugaAgent with both triggers. `--run-now` fires once; daemon mode keeps running. |
| `tools.py` | `fetch_rss` (stdlib XML + urllib) and `send_email` (stdlib smtplib). Zero extra deps beyond stdlib. |
| `config.json` | RSS sources, keywords, schedule, top-N, newsletter title, SMTP/LLM defaults. |
| `skills/newsletter_curation.md` | Editorial rules injected into every system prompt. |
| `_llm.py` | Multi-provider LLM factory (shared across all demo apps). |

---

## Data flow — scheduled run

```
1. [Monday 9:00 AM]  CronTrigger fires
   message: "Fetch latest from ArXiv, HuggingFace … filter for: LLM, agents, RAG …
             curate top 12 … compose HTML … send via email"

2. CugaAgent ReAct loop:
   ├─► fetch_rss("https://arxiv.org/rss/cs.AI", keywords=[...])  → 18 items
   ├─► fetch_rss("https://huggingface.co/blog/feed.xml", ...)    → 9 items
   └─► fetch_rss(... each source in config ...)

3. LLM applies (guided by newsletter_curation.md skill):
   - Semantic relevance filtering
   - Dedup against prior issues (from checkpoint thread history)
   - Rank + select top 12
   - Compose styled HTML newsletter

4. send_email(subject="AI Digest — Apr 1", html_body="<html>...")
   └─► SMTP → recipients in NEWSLETTER_TO

5. Full conversation (including selected URLs) written to checkpoint
   → used by LLM to avoid re-selecting the same articles next week
```

## Data flow — on-demand webhook

```
curl -X POST http://127.0.0.1:8401/newsletter \
     -H 'Content-Type: application/json' \
     -d '{"message": "Send the newsletter now", "thread_id": "newsletter-ai-digest"}'

WebhookTrigger receives POST → emits TriggerEvent → same pipeline runs immediately
202 Accepted returned immediately; agent runs asynchronously
```

---

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LLM_PROVIDER` | yes | from `config.json` | `rits` \| `watsonx` \| `openai` \| etc. |
| `LLM_MODEL` | no | provider default | Override model name |
| `SMTP_USERNAME` | yes | — | Sender address |
| `SMTP_PASSWORD` | yes | — | SMTP/app password |
| `NEWSLETTER_TO` | yes | — | Comma-separated recipients |
| `SMTP_HOST` | no | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | no | `587` | SMTP port |
