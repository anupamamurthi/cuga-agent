"""
Self-Healing Home Server Monitor — OpenClaw-style.

Three concurrent concerns, all wired by CugaMonitorApp:

  1. Reactive monitoring  (CugaWatcher)
     Polls system metrics every 1 minute.
     Fires the agent only when CPU/RAM/disk/load thresholds are crossed.
     Two predicates on the same source: warning (cooldown) + critical (immediate).

  2. Scheduled morning briefing  (CugaHost + CronChannel)
     Fires the agent at 08:00 every day to produce a health summary.
     Delivered via EmailChannel (or LogChannel if SMTP not configured).

  3. Interactive chat  (ConversationGateway)
     Browser UI at http://host:port
     Ask anything: "what's eating my disk?", "is nginx up?", "why is the server slow?"

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider openai --model gpt-4o
    python app.py --no-chat            # watcher + briefing only (headless)
    python app.py --no-briefing        # watcher + chat only

Environment variables:
    LLM_PROVIDER          rits | watsonx | openai | anthropic | litellm | ollama
    LLM_MODEL             model override
    ALERT_TO              email address for alerts (requires SMTP vars too)
    SMTP_USERNAME         SMTP sender address
    SMTP_PASSWORD         SMTP password
    POLL_INTERVAL_MINUTES metric poll frequency (default: 1)
    ALERT_COOLDOWN_SECONDS min seconds between repeated alerts (default: 900)
    DISK_THRESHOLD        warn threshold % (default: 80)
    DISK_CRITICAL         critical threshold % (default: 90)
    CPU_THRESHOLD         warn threshold % (default: 75)
    CPU_CRITICAL          critical threshold % (default: 90)
    RAM_THRESHOLD         warn threshold % (default: 80)
    RAM_CRITICAL          critical threshold % (default: 92)
    BRIEFING_SCHEDULE     cron expression for morning briefing (default: 0 8 * * *)
    ALLOWED_SERVICES      comma-separated service names the agent may check
                          (default: nginx,postgres,redis,docker,sshd,cron)
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from agent import make_agent, make_watcher
from cuga_channels import CugaMonitorApp

app = CugaMonitorApp(
    agent=make_agent(),
    watcher_factory=make_watcher,
    pipelines_config=_EXAMPLE_DIR / "cuga_pipelines.yaml",
    state_dir=_EXAMPLE_DIR / ".cuga" / "host",
    runtime_id="server-briefing",
    runtime_factory="briefing",
    runtime_config=lambda: {
        "schedule": os.getenv("BRIEFING_SCHEDULE", "0 8 * * *"),
        "email":    os.getenv("ALERT_TO"),
    },
    chat_port=8767,
    title="Server Monitor",
)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Self-Healing Server Monitor — CugaWatcher + CugaAgent",
    )
    parser.add_argument("--host",          default="127.0.0.1")
    parser.add_argument("--port",          type=int, default=8767)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    parser.add_argument("--no-chat",       action="store_true")
    parser.add_argument("--no-briefing",   action="store_true")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if not args.no_chat:
        print(f"\n  Server Monitor  →  http://{args.host}:{args.port}\n")

    app.run(
        enable_chat=not args.no_chat,
        enable_briefing=not args.no_briefing,
        chat_host=args.host,
        chat_port=args.port,
    )
