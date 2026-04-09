# Stock Alert — cuga++ demo

Monitor crypto and stock prices in a browser UI. Ask market questions on demand,
or set a threshold alert that emails you when a price is crossed.

```
cd docs/examples/demo_apps/stock_alert
python main.py
```

Then open **http://127.0.0.1:18794**

---

## UI

Two cards in the browser:

### Market Query

Type any symbol and ask a free-form question. Quick chips for common queries.

```
Symbol: BTC   Type: Crypto

Question: What is the current price and 24h change?
          Is this a good entry point compared to recent range?
          Summarise the current market conditions for this asset.
```

The agent fetches live data and answers in plain language with prices and change % highlighted.

### Price Watch

Configure a background monitor. Runs every 5 minutes; sends an email alert when
the threshold is crossed.

```
Symbol: BTC   Type: Crypto   Direction: Above   Threshold: $90,000

[Start Watch]

● Watching BTC — alert ↑ above $90,000 · every 5 min · output: EmailChannel
```

Stop the watch at any time with the Stop button. Watch state is preserved on
page reload.

---

## Email alerts

Set these env vars before starting. Without them, alerts appear in the server
log instead of your inbox.

```bash
export SMTP_HOST=smtp.gmail.com        # or your SMTP server
export SMTP_USERNAME=you@example.com
export SMTP_PASSWORD=your_app_password
export ALERT_TO=you@example.com
```

---

## Run

```bash
python main.py                    # default port 18794
python main.py --port 8080
python main.py --provider anthropic
```

---

## Dependencies

```bash
pip install -r requirements.txt
pip install fastapi uvicorn       # for the web UI
```

Crypto prices use the CoinGecko public API — no key needed.
Stocks require an Alpha Vantage key (free tier):

```bash
export ALPHA_VANTAGE_API_KEY=your_key   # alphavantage.co
```

---

## Environment variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `litellm` \| `ollama` |
| `LLM_MODEL` | Model name override (optional) |
| `ALPHA_VANTAGE_API_KEY` | Required for stock quotes |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_USERNAME` | Sender email / SMTP login |
| `SMTP_PASSWORD` | SMTP password or app password |
| `ALERT_TO` | Recipient email for watch alerts |
