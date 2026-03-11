# `cuga watch` Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        cuga watch CLI                           │
│                                                                 │
│   Natural-language instruction  OR  --config watch_config.json  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ (one-time, at startup)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   WatchInstructionParser                        │
│                                                                 │
│   LLM call (WatsonX/OpenAI)  →  structured JSON  →  WatchConfig│
│   Falls back to regex heuristic if LLM unavailable             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       WatchConfig                               │
│                                                                 │
│   sources: [ { type, url, interval_minutes, extra } ]           │
│   condition: { type: keyword | always | custom, keywords: [] }  │
│   actions: [ { type: email | sms | log | agent_notify } ]       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ wired by WatchExecutor
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CugaWatcher                               │
│                  (asyncio event loop)                           │
│                                                                 │
│  ┌────────────────────────────────────────────────┐            │
│  │  @watcher.source(every_minutes=30)             │            │
│  │                                                │            │
│  │  facebook_group ──► Playwright scraper         │            │
│  │  web_page       ──► httpx fetch                │  emit      │
│  │  rss_feed       ──► httpx + XML parser         ├───────►    │
│  │                                                │  list[dict]│
│  └────────────────────────────────────────────────┘            │
│                                                                 │
│  ┌────────────────────────────────────────────────┐            │
│  │  @watcher.on(source, when=predicate)           │ ◄──────    │
│  │                                                │            │
│  │  WatchCondition.matches?                       │            │
│  │  ┌─ keyword:  any kw in item.text?             │            │
│  │  ├─ always:   non-empty?                       │            │
│  │  └─ custom:   eval(expression)?                │            │
│  │                                                │            │
│  │  ──► dispatch WatchActions ──────────────────► │            │
│  │       email (SMTP/Gmail)                       │            │
│  │       sms   (Twilio)                           │            │
│  │       log   (stdout)                           │            │
│  │       agent_notify (CugaAgent LLM call)        │            │
│  └────────────────────────────────────────────────┘            │
│                                                                 │
│  ┌────────────────────────────────────────────────┐            │
│  │  archive drain (every 2 min)                   │            │
│  │  buffer → watch_archive.jsonl                  │            │
│  └────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

## How it works

1. **Parse once** — at startup the LLM converts your natural-language instruction into a typed `WatchConfig`. No further LLM calls (unless you use `agent_notify`).
2. **Poll on a timer** — each source fires on its own `interval_minutes` schedule. Sources are independent asyncio tasks.
3. **Filter** — each emission passes through `WatchCondition`. Only matching items flow to actions.
4. **Act** — matching items are dispatched to every configured action in parallel.
5. **Archive** — all raw items are buffered in memory and flushed to `watch_archive.jsonl` every 2 minutes.

## Key files

| File | Role |
|------|------|
| `src/cuga/watch/models.py` | Pydantic models: `WatchConfig`, `WatchSource`, `WatchCondition`, `WatchAction` |
| `src/cuga/watch/parser.py` | LLM + heuristic parser: natural language → `WatchConfig` |
| `src/cuga/watch/executor.py` | Source adapters, condition filters, action dispatchers, `WatchExecutor` |
| `src/cuga/watcher.py` | `CugaWatcher` — asyncio event loop with `@source` / `@on` decorators |
| `src/cuga/cli.py` | `cuga watch` CLI entry point |

## Extending

### New source type

Add a fetch function in `executor.py` and a new branch in `_fetch_source()`:

```python
async def _fetch_source(source: WatchSource) -> list[dict]:
    if source.type == "facebook_group":
        return await _fetch_facebook_group(source)
    if source.type == "slack_channel":   # new
        return await _fetch_slack(source)
    return await _fetch_web_page(source)
```

Also add the new literal to `WatchSource.type` in `models.py`.

### New action type

Add a branch in `_dispatch_action()` in `executor.py`:

```python
elif action.type == "webhook":           # new
    await _post_webhook(action, body)
```

Also add the new literal to `WatchAction.type` in `models.py`.

### New condition type

Add a branch in `_matches()` and `_filter_matches()` in `executor.py`:

```python
if condition.type == "regex":
    import re
    return any(re.search(condition.expression, item.get("text", "")) for item in items)
```

## Watching multiple sources

Add multiple entries to `sources` in your `watch_config.json`. Each source polls independently on its own schedule:

```json
{
  "sources": [
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/GROUP_A",
      "name": "Group A",
      "interval_minutes": 30,
      "extra": { "session_file": "./fb_session.json" }
    },
    {
      "type": "facebook_group",
      "url": "https://www.facebook.com/groups/GROUP_B",
      "name": "Group B",
      "interval_minutes": 15,
      "extra": { "session_file": "./fb_session.json" }
    },
    {
      "type": "rss_feed",
      "url": "https://example.com/feed.rss",
      "name": "Local RSS",
      "interval_minutes": 10
    }
  ]
}
```

The same `condition` and `actions` apply to all sources. Each source gets its own watcher task and its own notification handler.
