# Stock Alert — Architecture

## What kind of app this is

A **scheduled monitor with on-demand query support**. Two modes share the same
agent and tools:

- **Watch mode** — a `CronChannel` fires on a schedule. The agent fetches a
  price and decides whether to surface an alert.
- **Query mode** — a `WebhookChannel` accepts HTTP POST requests. The agent
  answers market questions on demand.

The key design choice: **the app owns the schedule and delivery; the agent owns
the judgment**. The cron fires regardless. The agent decides whether the price
action is worth surfacing.

---

## Division of labor

```
[CronChannel]         fires every N minutes
      ↓
[CugaAgent]           fetches price, checks threshold, writes rationale
      ↓
[Output channels]     LogChannel / TelegramChannel / SMSChannel
```

The agent is not replacing a rule engine — the threshold check is trivially
expressible as `price > X`. The agent earns its place by:

1. **Contextualising** the move: "crossed above $90k — continuing a 4-day rally"
   vs. just "price is $90,412"
2. **Suppressing noise**: if not triggered, it logs one line and stops. No
   verbose output unless something is actually happening.
3. **Handling free-form queries** in query mode — "compare ETH and SOL" is not
   a rule, it's a question.

---

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point — CLI, `make_agent()`, both runtime builders |
| `skills/stock_alert.md` | Agent instructions: tool usage, alert format, query format |
| `requirements.txt` | Python dependencies |

---

## Agent tools

Provided by `cuga_channels.make_market_data_tools()`:

| Tool | Data source | Key required |
|---|---|---|
| `get_crypto_price` | CoinGecko public API | No |
| `get_stock_quote` | Alpha Vantage | Yes — `ALPHA_VANTAGE_API_KEY` |

---

## Watch mode data flow

```
CronChannel(schedule="*/5 * * * *", message="Check BTC price. Alert threshold: $90,000 (above).")
    → CugaAgent
        → get_crypto_price("BTC")
        → price > $90,000?
            yes → "PRICE ALERT\nBTC crossed above..."
            no  → "BTC at $88,200 — below alert. No action needed."
    → LogChannel (always)
    → TelegramChannel (if --telegram)
    → SMSChannel (if --sms)
```

## Query mode data flow

```
WebhookChannel(POST /market {"query": "compare ETH and SOL"})
    → CugaAgent
        → get_crypto_price("ETH")
        → get_crypto_price("SOL")
        → "ETH $3,410 (+1.2% 24h)  ·  SOL $142 (-0.4% 24h)"
    → LogChannel
```

---

## Why no CugaHost or pipelines

Watch mode runs a single agent on a single symbol with a single schedule.
`CugaRuntime` is the right level of abstraction — it's lighter than `CugaHost`
(which adds multi-app routing) and more appropriate for a focused, self-contained
monitor.

Query mode uses `WebhookChannel` directly — no buffering, no cron, just
request → agent → response.

If you extended this to monitor a whole portfolio (multiple symbols, multiple
schedules, user-managed watchlists), `CugaHost` with registered apps would make
sense. For a single-symbol demo, it's unnecessary overhead.

---

## Output channels

| Channel | When to use |
|---|---|
| `LogChannel` | Always present — stdout for development and CI |
| `TelegramChannel` | `--telegram` flag + `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` set |
| `SMSChannel` | `--sms` flag + all `TWILIO_*` env vars set |

Multiple output channels can be active simultaneously. The same alert is
delivered to all of them.
