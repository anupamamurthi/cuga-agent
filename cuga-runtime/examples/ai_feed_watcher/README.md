# Use Case: AI Feed Watcher

Monitors 14 RSS/Atom feeds from leading AI research blogs and newsletters. Every new post is an event. CUGA decides if it's relevant to agentic AI and rates the signal strength. High/medium signal posts surface in the UI.

No API key required — all feeds are publicly accessible.

---

## Why this is a good event-driven use case

- **Posts arrive asynchronously** — you can't block waiting for LLM analysis of every item
- **Volume is bursty** — a major release from Anthropic or OpenAI can trigger 5+ posts in minutes; the queue absorbs it
- **Filtering is subjective** — exactly the kind of nuanced judgement LLMs excel at ("is this actually about agentic AI, or just a benchmark?")
- **Clean separation of concerns** — the feed fetcher knows nothing about CUGA; CUGA knows nothing about RSS

---

## Architecture

```
╔══════════════════════════════════════════════════════════════╗
║  RSS / Atom Feeds (publicly accessible, no auth)             ║
║                                                              ║
║  Anthropic Blog  ·  OpenAI Blog  ·  Google DeepMind         ║
║  Hugging Face  ·  Lilian Weng  ·  Simon Willison            ║
║  Import AI  ·  One Useful Thing  ·  Interconnects           ║
║  BAIR Blog  ·  AI Snake Oil  ·  Sebastian Raschka  ·  …     ║
╚══════════════════════════╤═══════════════════════════════════╝
                           │  HTTP GET (XML)
                           ▼
╔══════════════════════════════════════════════════════════════╗
║  Feed Watcher Service  (port 8002)                           ║
║  examples/ai_feed_watcher/service.py                         ║
║                                                              ║
║  GET  /              Serve the UI                            ║
║  POST /fetch         Fetch & parse RSS feeds                 ║
║       preview_only=true  → return posts to browser           ║
║       preview_only=false → submit directly to cuga-runtime   ║
║  GET  /runtime/*     Proxy all cuga-runtime calls            ║
║       (avoids browser CORS — single origin for the UI)       ║
║                                                              ║
║  Parses RSS 2.0 and Atom, strips HTML, filters by date.     ║
║  Serves AI_RELEVANCE_PROMPT alongside posts so the UI        ║
║  can include it when submitting events.                      ║
╚══════════════════════════╤═══════════════════════════════════╝
                           │  POST /events  { query, trigger, context }
                           │  (proxied: browser → watcher → runtime)
                           ▼
╔══════════════════════════════════════════════════════════════╗
║  cuga-runtime  (port 8001)                                   ║
║                                                              ║
║  FastAPI        TaskQueue           TaskWorker               ║
║  POST /events ──► asyncio.Queue ──► pull event              ║
║  GET  /events  ◄─ dict[results] ◄── write result            ║
║                                     │                        ║
║                                     │ builds query:          ║
║                                     │  <AI_RELEVANCE_PROMPT> ║
║                                     │  Context:              ║
║                                     │    feed_name: Anthropic ║
║                                     │    title: "Claude…"    ║
║                                     │    text: "We've…"      ║
╚══════════════════════════════════════╤═══════════════════════╝
                                       │  HTTP POST /stream
                                       ▼
╔══════════════════════════════════════════════════════════════╗
║  CUGA  (port 7860)                                           ║
║                                                              ║
║  Receives prompt + post as a single natural-language string. ║
║  Reasons about it and streams back:                          ║
║                                                              ║
║    event: Answer                                             ║
║    data: { "data": "relevant: yes\n                          ║
║                     signal: high\n                           ║
║                     summary: …" }                            ║
╚══════════════════════════╤═══════════════════════════════════╝
                           │  assessment stored in cuga-runtime result dict
                           ▼
╔══════════════════════════════════════════════════════════════╗
║  Browser UI  (served from port 8002)                         ║
║                                                              ║
║  Polls GET /runtime/events every 4s.                         ║
║  Parses output → relevant badge, signal strength, summary.  ║
║  Sidebar shows per-feed counts.                              ║
║  Each card has an individual "Analyze" button or batch all.  ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Data flow — one post, end to end

```
1. User clicks "Fetch Feeds"
   → POST /fetch (preview_only=true) to feed-watcher
   → feed-watcher fetches all 14 RSS/Atom feeds in parallel
   → parses XML, filters to last N days
   → returns posts + AI_RELEVANCE_PROMPT to browser

2. Posts appear as "fetched" cards in the UI
   → each card shows: feed name, title, timestamp
   → each card has an "Analyze" button

3. User clicks "Analyze" on a card
   → browser POSTs to /runtime/events (proxied through watcher → cuga-runtime)
   → payload: { query: AI_RELEVANCE_PROMPT, trigger: "rss.new_post.anthropic", context: {…} }
   → cuga-runtime returns 202 + task_id immediately
   → card status changes to "queued"

4. Poll loop (every 4s)
   → GET /runtime/events?limit=200
   → card updates: queued → running → completed

5. CUGA assessment arrives
   → cuga-runtime worker called CUGA /stream with query + folded context
   → CUGA streamed back: relevant: yes / signal: high / summary: …
   → card shows green "✓ relevant" badge, signal color, one-line summary
```

---

## What CUGA sees for one post

```
You are monitoring AI research blogs and newsletters for signal about agentic AI.
Given the post below, decide:
1. Is this relevant to agentic AI? (yes/no) — be strict: general LLM benchmarks alone are not agentic AI
2. If yes: one-sentence summary of the key insight
3. If yes: signal strength (high / medium / low) — high means genuinely new, actionable, or surprising
4. If no: one-word reason why not (e.g. politics, personal, benchmark, unrelated)

Reply in this exact format:
relevant: yes|no
signal: high|medium|low  (omit if not relevant)
summary: <one sentence>  (omit if not relevant)
reason: <one word>       (omit if relevant)

Context:
  feed_name: Anthropic Blog
  title: Claude can now use tools in parallel
  text: We've updated Claude to support parallel tool use, enabling agents to call
        multiple tools simultaneously rather than sequentially…
  url: https://www.anthropic.com/blog/tool-use
  posted_at: 2025-04-10T14:00:00+00:00
```

---

## Signal classification

| Signal | Meaning | Example |
|--------|---------|---------|
| **high** | New, actionable, or surprising | Anthropic releases parallel tool use for agents |
| **medium** | Useful context, not novel | Overview of agent memory patterns |
| **low** | Tangentially relevant | Benchmark result that mentions agents once |
| *(none / irrelevant)* | Not about agentic AI | Policy post, general LLM eval, unrelated topic |

---

## Running it

```bash
# 1. Start CUGA (the AI engine) — port 7860

# 2. Start cuga-runtime — the async queue layer
cd cuga-runtime
uvicorn cuga_runtime.app:app --port 8001 --reload

# 3. Start the feed watcher service — serves the UI + fetches RSS
uvicorn examples.ai_feed_watcher.service:app --port 8002 --reload

# 4. Open the UI
open http://localhost:8002
```

Then:
1. Click **Connect** to verify both dots go green
2. Choose a lookback window (1 / 3 / 7 / 30 days)
3. Click **Fetch Feeds** — posts appear from all 14 feeds
4. Click **Analyze** on individual cards, or **Analyze (N)** to queue all
5. Watch cards update as CUGA processes each post

---

## Files

| File | What it does |
|------|-------------|
| `watched_accounts.py` | 14 RSS feed definitions + `AI_RELEVANCE_PROMPT` |
| `service.py` | FastAPI service: fetches RSS, serves UI, proxies runtime calls |
| `ui/index.html` | Single-page UI: fetch → analyze → view assessments live |

---

## Feeds watched

| Feed | Source slug |
|------|------------|
| Anthropic Blog | `anthropic` |
| OpenAI Blog | `openai` |
| Google DeepMind | `deepmind` |
| Hugging Face Blog | `huggingface` |
| The Batch (Andrew Ng) | `the_batch` |
| Lilian Weng | `lilianweng` |
| Simon Willison | `simonwillison` |
| Import AI (Jack Clark) | `import_ai` |
| One Useful Thing (Ethan Mollick) | `mollick` |
| Interconnects (Nathan Lambert) | `interconnects` |
| BAIR Blog | `bair` |
| AI Snake Oil | `aisnakeoil` |
| Sebastian Raschka | `raschka` |
| Last Week in AI | `lastweekinai` |

---

## Extending this

- **Push notifications** — add `callback_url` pointing to a Slack webhook; CUGA-runtime will POST high-signal results there without any polling
- **Per-feed threads** — pass a stable `thread_id` per feed so CUGA builds a running digest rather than assessing each post cold
- **Scale out** — swap `asyncio.Queue` for Redis Streams or Postgres (Phase 2 of cuga-runtime) to watch 100+ feeds across multiple workers
- **Add more feeds** — edit `watched_accounts.py`; no other changes needed
