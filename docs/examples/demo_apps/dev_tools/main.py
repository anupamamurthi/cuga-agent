"""
Dev Tools Agent — developer assistant powered by cuga++
=======================================================

Two modes — same agent, same tools:

  🔍 Audit mode  (CronChannel + shell tools)
     Scheduled security and dependency audit: runs pip list --outdated, safety
     check, and coverage report.  Posts results to Slack or email.
     Default: weekly on Mondays at 9am.

  💬 Query mode  (WebhookChannel + shell tools)
     POST {"query": "are any packages outdated?"} → instant response.
     Great for CI/CD integration or developer chat bots.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • make_shell_tools() returns 3 tools — run_shell_command,
    check_python_dependencies, run_test_coverage.
  • All shell commands are allow-listed for security — only safe read-only
    commands (pip list, safety check, pytest --co, coverage report, etc.)
  • CronChannel triggers weekly audits automatically.
  • WebhookChannel answers developer queries on demand.
  • Slack and email output are one-line swaps.

─────────────────────────────────────────────────────────────────────────────

Prerequisites:
    No required env vars for basic operation (tools use subprocess/stdlib).

    Optional — safety (security audit):
        pip install safety

    Optional — coverage (test coverage):
        pip install coverage
        coverage run -m pytest   # generate .coverage data

    Slack output (optional):
        export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

    Email output (optional):
        export SMTP_HOST=smtp.gmail.com
        export SMTP_USERNAME=you@gmail.com
        export SMTP_PASSWORD=your_app_password
        export EMAIL_TO=you@example.com

Run:
    python main.py --audit                              # weekly audit (log output)
    python main.py --audit --now                        # fire immediately (testing)
    python main.py --audit --slack                      # post results to Slack
    python main.py --query                              # on-demand webhook
    python main.py --audit --now --query                # both modes
    python main.py --provider anthropic --audit --now

Query mode:
    curl -X POST http://localhost:18795/devtools \\
         -H "Content-Type: application/json" \\
         -d '{"query": "are any Python packages outdated?"}'

    curl -X POST http://localhost:18795/devtools \\
         -H "Content-Type: application/json" \\
         -d '{"query": "check for security vulnerabilities"}'

    curl -X POST http://localhost:18795/devtools \\
         -H "Content-Type: application/json" \\
         -d '{"query": "list all the tests we have"}'

    curl -X POST http://localhost:18795/devtools \\
         -H "Content-Type: application/json" \\
         -d '{"query": "show me the git log"}'

Environment variables:
    LLM_PROVIDER         rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL            model override
    SLACK_WEBHOOK_URL    Slack incoming webhook (for --slack output)
    SMTP_HOST            SMTP server (for --email output)
    SMTP_USERNAME        SMTP login (for --email output)
    SMTP_PASSWORD        SMTP password (for --email output)
    EMAIL_TO             Recipient address (for --email output)
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
    from cuga_channels import make_shell_tools
    from _llm import create_llm

    skills_dir = _DIR / "skills"
    plugins = []
    if skills_dir.exists():
        try:
            from cuga_skills import CugaSkillsPlugin
            plugins = [CugaSkillsPlugin(skills_dir=str(skills_dir))]
        except ImportError:
            pass

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=make_shell_tools(),   # run_shell_command + check_python_dependencies + run_test_coverage
        plugins=plugins,
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


def _make_output_channels(use_slack: bool, use_email: bool):
    from cuga_channels import LogChannel
    channels = [LogChannel()]

    if use_slack:
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if webhook:
            from cuga_channels import SlackChannel
            channels.append(SlackChannel(webhook_url=webhook))
            log.info("Slack output enabled")
        else:
            log.warning("--slack set but SLACK_WEBHOOK_URL missing — logging only")

    if use_email:
        host = os.getenv("SMTP_HOST")
        user = os.getenv("SMTP_USERNAME")
        pwd  = os.getenv("SMTP_PASSWORD")
        to   = os.getenv("EMAIL_TO")
        if host and user and pwd and to:
            from cuga_channels import EmailChannel
            channels.append(EmailChannel(
                to=to,
                subject="Dev Tools Audit Report",
                smtp_host=host,
                smtp_username=user,
                smtp_password=pwd,
            ))
            log.info("Email output enabled → %s", to)
        else:
            log.warning("--email set but SMTP env vars missing — logging only")

    return channels


# ---------------------------------------------------------------------------
# Mode: audit — scheduled security/dependency audit
# ---------------------------------------------------------------------------

_AUDIT_MESSAGE = """
Perform a full developer tooling audit. Run all three of these checks in order:

1. Call check_python_dependencies() to get a list of outdated packages and any
   known security vulnerabilities.

2. Call run_test_coverage() to check test coverage or list available test cases.

3. Call run_shell_command("git log --oneline -20") to show recent commit history.

Then produce a structured audit report:

## Dependency Health
- List outdated packages (name, current, latest version)
- List any security vulnerabilities found
- Recommend action items

## Test Coverage
- Summarise coverage percentages or list test count
- Flag any modules with low coverage (<80%)

## Recent Activity
- Summarise the last 20 commits in 2-3 sentences

Keep the report under 600 words. Use bullet points for action items.
""".strip()


def build_audit_runtime(agent, schedule: str, output_channels):
    from cuga_channels import CugaRuntime, CronChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(schedule=schedule, message=_AUDIT_MESSAGE)
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="dev-tools-audit",
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
                path="/devtools",
                message_template=(
                    "Developer assistant query:\n\n"
                    "{payload}\n\n"
                    "Use the available shell tools to answer the query:\n"
                    "- run_shell_command: run a specific safe command\n"
                    "- check_python_dependencies: audit packages and security\n"
                    "- run_test_coverage: check test coverage or list tests\n"
                    "Be direct and include the actual command output in your answer."
                ),
            )
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="dev-tools-query",
    )


# ---------------------------------------------------------------------------
# Mode: both
# ---------------------------------------------------------------------------

def build_both_runtime(agent, schedule: str, port: int, output_channels):
    from cuga_channels import CugaRuntime, CronChannel, WebhookChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(schedule=schedule, message=_AUDIT_MESSAGE),
            WebhookChannel(
                port=port,
                path="/devtools",
                message_template=(
                    "Developer query:\n{payload}\n\n"
                    "Use shell tools to answer concisely."
                ),
            ),
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="dev-tools-both",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(mode: str, schedule: str, port: int, now: bool,
              use_slack: bool, use_email: bool):
    agent   = make_agent()
    outputs = _make_output_channels(use_slack, use_email)

    if now:
        schedule = "* * * * *"

    if mode == "audit":
        runtime = build_audit_runtime(agent, schedule, outputs)
        print(f"\n  Dev Tools Agent  —  Audit mode")
        print(f"  {'─' * 48}")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: firing every minute for testing)")
        print(f"  {'─' * 48}\n")

    elif mode == "query":
        runtime = build_query_runtime(agent, port, outputs)
        print(f"\n  Dev Tools Agent  —  Query mode")
        print(f"  {'─' * 48}")
        print(f"  Endpoint : http://localhost:{port}/devtools")
        print(f"  {'─' * 48}")
        print(f"\n  Example queries:")
        print(f'    curl -X POST http://localhost:{port}/devtools \\')
        print(f'         -H "Content-Type: application/json" \\')
        print(f"         -d '{{\"query\": \"are any packages outdated?\"}}'\n")

    else:  # both
        runtime = build_both_runtime(agent, schedule, port, outputs)
        print(f"\n  Dev Tools Agent  —  Audit + Query mode")
        print(f"  {'─' * 48}")
        print(f"  Audit    : {schedule}")
        print(f"  Endpoint : http://localhost:{port}/devtools")
        if now:
            print(f"  (--now: audit fires every minute for testing)")
        print(f"  {'─' * 48}\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Dev Tools Agent — dependency audits and developer assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--audit",     action="store_true",
                        help="Audit mode: scheduled security/dependency check")
    parser.add_argument("--query",     action="store_true",
                        help="Query mode: answer dev queries via HTTP POST")
    parser.add_argument("--schedule",  "-s", default="0 9 * * 1",
                        help="Cron schedule for audit mode (default: Mondays at 9am)")
    parser.add_argument("--port",      type=int, default=18795,
                        help="Webhook port for query mode (default: 18795)")
    parser.add_argument("--now",       action="store_true",
                        help="Fire every minute instead of on schedule (for testing)")
    parser.add_argument("--slack",     action="store_true",
                        help="Post to Slack (needs SLACK_WEBHOOK_URL)")
    parser.add_argument("--email",     action="store_true",
                        help="Send via email (needs SMTP_* env vars)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if args.audit and args.query:
        mode = "both"
    elif args.query:
        mode = "query"
    else:
        mode = "audit"   # default: most useful for automated use

    asyncio.run(run(mode, args.schedule, args.port, args.now, args.slack, args.email))


if __name__ == "__main__":
    main()
