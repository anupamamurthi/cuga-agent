"""
CI/CD Failure Alerter — cuga++ demo
=====================================

WebhookChannel receives a GitHub Actions event.
CugaAgent analyses the payload.
Alert is printed to the terminal (+ emailed if SMTP is configured).

Run:
    python main.py
    python main.py --provider anthropic
    python main.py --provider openai --model gpt-4o
    python main.py --port 18792

Then simulate a GitHub webhook:
    curl -s -X POST http://localhost:18791/webhook \\
         -H 'Content-Type: application/json' \\
         -d @examples/failure.json

Environment variables:
    LLM_PROVIDER         rits | anthropic | openai | watsonx | litellm | ollama
    LLM_MODEL            model override
    ALERT_TO             email address (requires SMTP vars)
    SMTP_USERNAME        SMTP sender address
    SMTP_PASSWORD        SMTP password
    WEBHOOK_PORT         port to listen on (default: 18791)
    WEBHOOK_SECRET       optional bearer token for auth
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
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=[],   # no tools needed — pure analysis
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(port: int):
    from cuga_channels import CugaRuntime, WebhookChannel, LogChannel, smart_deliver

    agent = make_agent()

    runtime = CugaRuntime(
        agent=agent,
        input_channels=[
            WebhookChannel(
                port=port,
                path="/webhook",
                message_template=(
                    "A GitHub Actions event was received. Analyse it and produce a "
                    "concise alert report.\n\nPayload:\n{payload}"
                ),
                secret_token=os.getenv("WEBHOOK_SECRET"),
            )
        ],
        output_channels=[LogChannel()],
        require_buffer=False,
        thread_id="cicd-alerter",
        on_output=lambda answer, _meta: asyncio.ensure_future(
            smart_deliver(
                answer,
                subject_prefix="🚨 CI/CD Alert",
                metadata={"subject": "🚨 CI/CD build failure"},
            )
        ),
    )

    print(f"\n  CI/CD Alerter  →  listening on http://localhost:{port}/webhook")
    print(  "  Waiting for GitHub webhook POSTs...\n")
    print(  "  Test with:")
    print(f"    curl -s -X POST http://localhost:{port}/webhook \\")
    print(  "         -H 'Content-Type: application/json' \\")
    print(  "         -d @examples/failure.json\n")

    await runtime.launch()


def main():
    parser = argparse.ArgumentParser(description="CI/CD Failure Alerter — cuga++ demo")
    parser.add_argument("--provider", "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    parser.add_argument("--port",          type=int, default=int(os.getenv("WEBHOOK_PORT", "18791")))
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    asyncio.run(run(args.port))


if __name__ == "__main__":
    main()
