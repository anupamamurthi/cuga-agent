"""
Stock Alert Agent — live market price monitor powered by cuga++
===============================================================

Two modes — same agent, same tools:

  📈 Watch mode  (CronChannel + market data tools)
     Polls every N minutes. If a price threshold is crossed, sends an alert
     via Telegram or SMS.  Default: check BTC every 5 minutes.

  🔍 Query mode  (WebhookChannel + market data tools)
     POST {"query": "what is BTC price?"} → instant response with price + 24h change.
     Great for Slack bots, dashboards, or ad-hoc queries.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • make_market_data_tools() returns 2 tools — get_crypto_price, get_stock_quote.
  • CronChannel polls on a schedule; the agent decides whether to alert.
  • WebhookChannel answers on-demand queries instantly.
  • Swapping Telegram → SMS output is one line change.

─────────────────────────────────────────────────────────────────────────────

Prerequisites:
    Crypto (no key needed — CoinGecko public API):
        No setup required.

    Stocks (requires Alpha Vantage free API key):
        export ALPHA_VANTAGE_API_KEY=your_key   # get at alphavantage.co

    Telegram output (optional):
        export TELEGRAM_BOT_TOKEN=123456789:AAF...
        export TELEGRAM_CHAT_ID=987654321

    SMS output via Twilio (optional):
        export TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxx
        export TWILIO_AUTH_TOKEN=your_auth_token
        export TWILIO_FROM=+15559876543
        export SMS_TO=+15551234567

Run:
    python main.py --watch                           # watch BTC, log output
    python main.py --watch --now                     # fire immediately (testing)
    python main.py --watch --symbol ETH              # watch Ethereum
    python main.py --watch --symbol BTC --above 50000 --telegram   # BTC > $50k alert
    python main.py --watch --symbol BTC --below 40000 --sms        # BTC < $40k via SMS
    python main.py --watch --symbol AAPL --stock     # watch a stock
    python main.py --query                           # on-demand query webhook
    python main.py --provider anthropic --watch --now

Query mode:
    curl -X POST http://localhost:18794/market \\
         -H "Content-Type: application/json" \\
         -d '{"query": "what is the current Bitcoin price?"}'

    curl -X POST http://localhost:18794/market \\
         -H "Content-Type: application/json" \\
         -d '{"query": "compare ETH and SOL prices"}'

    curl -X POST http://localhost:18794/market \\
         -H "Content-Type: application/json" \\
         -d '{"query": "get me a quote for TSLA"}'

Environment variables:
    LLM_PROVIDER              rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL                 model override
    ALPHA_VANTAGE_API_KEY     Alpha Vantage key (for stock quotes)
    TELEGRAM_BOT_TOKEN        Telegram bot token (for --telegram output)
    TELEGRAM_CHAT_ID          Telegram chat ID (for --telegram output)
    TWILIO_ACCOUNT_SID        Twilio SID (for --sms output)
    TWILIO_AUTH_TOKEN         Twilio auth token (for --sms output)
    TWILIO_FROM               Sender phone number (for --sms output)
    SMS_TO                    Recipient phone number (for --sms output)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_DIR       = Path(__file__).parent
_DEMOS_DIR = _DIR.parent

for _p in [str(_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def make_agent():
    from cuga import CugaAgent
    from cuga_channels import make_market_data_tools
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=make_market_data_tools(),   # get_crypto_price + get_stock_quote
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


def _make_output_channels(use_telegram: bool, use_sms: bool):
    from cuga_channels import LogChannel
    channels = [LogChannel()]

    if use_telegram:
        tok = os.getenv("TELEGRAM_BOT_TOKEN")
        cid = os.getenv("TELEGRAM_CHAT_ID")
        if tok and cid:
            from cuga_channels import TelegramChannel
            channels.append(TelegramChannel(token=tok, chat_id=cid))
            log.info("Telegram output enabled → chat %s", cid)
        else:
            log.warning("--telegram set but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing — logging only")

    if use_sms:
        sid  = os.getenv("TWILIO_ACCOUNT_SID")
        auth = os.getenv("TWILIO_AUTH_TOKEN")
        frm  = os.getenv("TWILIO_FROM")
        to   = os.getenv("SMS_TO")
        if sid and auth and frm and to:
            from cuga_channels import SMSChannel
            channels.append(SMSChannel(to=to, from_=frm, account_sid=sid, auth_token=auth))
            log.info("SMS output enabled → %s", to)
        else:
            log.warning("--sms set but Twilio env vars missing — logging only")

    return channels


# ---------------------------------------------------------------------------
# Mode: watch — poll on a schedule and alert on threshold crossing
# ---------------------------------------------------------------------------

def _build_watch_message(symbol: str, threshold: float | None, direction: str, is_stock: bool) -> str:
    asset_type = "stock" if is_stock else "cryptocurrency"
    tool_call  = f"get_stock_quote('{symbol}')" if is_stock else f"get_crypto_price('{symbol}')"

    lines = [
        f"Check the current price of {symbol} by calling {tool_call}.",
        "",
    ]

    if threshold is not None:
        if direction == "above":
            lines += [
                f"If the price is ABOVE ${threshold:,.2f}, respond with a clear PRICE ALERT message:",
                f"  - State that {symbol} has crossed above ${threshold:,.2f}",
                f"  - Include the exact current price and 24h change",
                f"  - Suggest this may be a significant move worth attention",
                "",
                f"If the price is BELOW ${threshold:,.2f}, just reply with a brief status: "
                f"'{symbol} at $[price] — below alert threshold of ${threshold:,.2f}. No action needed.'",
            ]
        else:  # below
            lines += [
                f"If the price is BELOW ${threshold:,.2f}, respond with a clear PRICE ALERT message:",
                f"  - State that {symbol} has dropped below ${threshold:,.2f}",
                f"  - Include the exact current price and 24h change",
                f"  - Note this may be a significant move worth attention",
                "",
                f"If the price is ABOVE ${threshold:,.2f}, just reply with a brief status: "
                f"'{symbol} at $[price] — above floor threshold of ${threshold:,.2f}. No action needed.'",
            ]
    else:
        lines.append(
            f"Report the current {symbol} price with the 24h change in a concise one-liner."
        )

    return "\n".join(lines)


def build_watch_runtime(agent, schedule: str, symbol: str, threshold: float | None,
                        direction: str, is_stock: bool, output_channels):
    from cuga_channels import CugaRuntime, CronChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=_build_watch_message(symbol, threshold, direction, is_stock),
            )
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id=f"stock-alert-{symbol.lower()}",
    )


# ---------------------------------------------------------------------------
# Mode: query — on-demand via webhook
# ---------------------------------------------------------------------------

def build_query_runtime(agent, port: int, output_channels):
    from cuga_channels import CugaRuntime, WebhookChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            WebhookChannel(
                port=port,
                path="/market",
                message_template=(
                    "Market data query:\n\n"
                    "{payload}\n\n"
                    "Extract the query and answer it using the available market tools.\n"
                    "For crypto: use get_crypto_price with the appropriate symbol.\n"
                    "For stocks: use get_stock_quote with the ticker symbol.\n"
                    "Be concise and include the price, change, and any other relevant data."
                ),
            )
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="stock-alert-query",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(mode: str, schedule: str, symbol: str, threshold: float | None,
              direction: str, is_stock: bool, port: int, now: bool,
              use_telegram: bool, use_sms: bool):
    agent   = make_agent()
    outputs = _make_output_channels(use_telegram, use_sms)

    if now:
        schedule = "* * * * *"

    if mode == "watch":
        runtime = build_watch_runtime(agent, schedule, symbol, threshold, direction, is_stock, outputs)

        print(f"\n  Stock Alert Agent  —  Watch mode")
        print(f"  {'─' * 48}")
        print(f"  Symbol   : {symbol}")
        print(f"  Type     : {'stock' if is_stock else 'crypto'}")
        if threshold is not None:
            print(f"  Threshold: ${threshold:,.2f} ({direction})")
        else:
            print(f"  Threshold: none (status report every poll)")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: firing every minute for testing)")
        print(f"  {'─' * 48}\n")

    else:  # query
        runtime = build_query_runtime(agent, port, outputs)

        print(f"\n  Stock Alert Agent  —  Query mode")
        print(f"  {'─' * 48}")
        print(f"  Endpoint : http://localhost:{port}/market")
        print(f"  {'─' * 48}")
        print(f"\n  Example queries:")
        print(f'    curl -X POST http://localhost:{port}/market \\')
        print(f'         -H "Content-Type: application/json" \\')
        print(f"         -d '{{\"query\": \"what is the current Bitcoin price?\"}}'")
        print(f"\n    curl -X POST http://localhost:{port}/market \\")
        print(f'         -H "Content-Type: application/json" \\')
        print(f"         -d '{{\"query\": \"TSLA stock quote\"}}'\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Stock Alert Agent — crypto and stock price monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--watch",     action="store_true",
                        help="Watch mode: poll on schedule and alert on threshold")
    parser.add_argument("--query",     action="store_true",
                        help="Query mode: answer market queries via HTTP POST")
    parser.add_argument("--symbol",    "-s", default="BTC",
                        help="Ticker symbol to watch (default: BTC)")
    parser.add_argument("--stock",     action="store_true",
                        help="Treat symbol as a stock ticker (uses Alpha Vantage)")
    parser.add_argument("--threshold", "-t", type=float, default=None,
                        help="Price threshold for alert (e.g. 50000)")
    parser.add_argument("--above",     action="store_true",
                        help="Alert when price rises ABOVE threshold")
    parser.add_argument("--below",     action="store_true",
                        help="Alert when price falls BELOW threshold")
    parser.add_argument("--schedule",  default="*/5 * * * *",
                        help="Cron schedule for watch mode (default: every 5 minutes)")
    parser.add_argument("--port",      type=int, default=18794,
                        help="Webhook port for query mode (default: 18794)")
    parser.add_argument("--now",       action="store_true",
                        help="Fire every minute instead of on schedule (for testing)")
    parser.add_argument("--telegram",  action="store_true",
                        help="Deliver to Telegram (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")
    parser.add_argument("--sms",       action="store_true",
                        help="Deliver via SMS/Twilio (needs TWILIO_* env vars)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    direction = "above" if args.above else "below"
    if args.threshold and not (args.above or args.below):
        direction = "above"   # sensible default

    mode = "query" if args.query else "watch"   # default: watch

    asyncio.run(run(
        mode      = mode,
        schedule  = args.schedule,
        symbol    = args.symbol.upper(),
        threshold = args.threshold,
        direction = direction,
        is_stock  = args.stock,
        port      = args.port,
        now       = args.now,
        use_telegram = args.telegram,
        use_sms   = args.sms,
    ))


if __name__ == "__main__":
    main()
