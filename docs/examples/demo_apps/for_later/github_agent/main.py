"""
GitHub Agent — GitHub API assistant powered by cuga++
======================================================

Two modes — same agent, same tools:

  📋 PR digest  (CronChannel + GitHub tools)
     Daily pull request review digest: what needs review, what's ready to merge.
     Default: weekdays at 9am.  Output: Slack, Email, or log.

  🚨 CI/CD alert  (WebhookChannel + GitHub tools)
     GitHub Actions webhook → agent analyses failure → posts summary to Slack.
     Optionally posts a comment directly on the failing PR.

  🔀 Both modes at once  (--both)
     Daily PR digest AND CI/CD failure alerts.

─────────────────────────────────────────────────────────────────────────────
What this shows about cuga++
─────────────────────────────────────────────────────────────────────────────

  • make_github_tools() returns 5 tools — list_pull_requests, get_pull_request,
    list_issues, get_workflow_runs, create_issue_comment.
  • WebhookChannel receives GitHub Actions payloads; agent decides what to say.
  • Same agent answers both "what PRs are open?" and "what broke in CI?"
  • Swapping Slack → Email → Telegram output is one line change.

─────────────────────────────────────────────────────────────────────────────

Prerequisites:
    1. GitHub Personal Access Token:
       github.com → Settings → Developer settings → Personal access tokens
       Scopes: repo (private) or public_repo (public)
       export GITHUB_TOKEN=ghp_...

    2. Or if you have the GitHub CLI already authenticated:
       export GITHUB_TOKEN=$(gh auth token)

Run:
    python main.py --digest                         # PR digest (logs output)
    python main.py --digest --now                   # fire right now for testing
    python main.py --cicd                           # CI/CD webhook listener
    python main.py --both --now                     # both modes
    python main.py --digest --repo owner/myrepo     # specify repo
    python main.py --digest --provider anthropic    # use Claude

Webhook mode (receive GitHub Actions events):
    # Send a simulated failure payload:
    curl -X POST http://localhost:18793/github \\
         -H "Content-Type: application/json" \\
         -d '{
           "action": "completed",
           "workflow_run": {
             "name": "CI",
             "conclusion": "failure",
             "head_branch": "main",
             "html_url": "https://github.com/owner/repo/actions/runs/12345"
           },
           "repository": {"full_name": "owner/repo"}
         }'

    Or configure GitHub Actions to POST to your server:
    # In your workflow .yml:
    # on: [workflow_run]
    # Use ngrok for local testing: ngrok http 18793

With Slack output:
    export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
    python main.py --digest --now --slack

Environment variables:
    LLM_PROVIDER         rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL            model override
    GITHUB_TOKEN         Personal Access Token (required)
    GITHUB_DEFAULT_REPO  Default repo in owner/repo format
    SLACK_WEBHOOK_URL    Slack incoming webhook (for --slack output)
    TELEGRAM_BOT_TOKEN   Telegram bot token (for --telegram output)
    TELEGRAM_CHAT_ID     Telegram chat ID (for --telegram output)
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

def make_agent(default_repo: str):
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from cuga_channels import make_github_tools
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=make_github_tools(default_repo=default_repo),   # 5 GitHub tools
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


def _make_output_channels(use_slack: bool, use_telegram: bool):
    from cuga_channels import LogChannel
    channels = [LogChannel()]

    if use_slack:
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if webhook:
            from cuga_channels import SlackChannel
            channels.append(SlackChannel(webhook_url=webhook))
            log.info("Slack output enabled")
        else:
            log.warning("--slack set but SLACK_WEBHOOK_URL missing — using log only")

    if use_telegram:
        tok = os.getenv("TELEGRAM_BOT_TOKEN")
        cid = os.getenv("TELEGRAM_CHAT_ID")
        if tok and cid:
            from cuga_channels import TelegramChannel
            channels.append(TelegramChannel(token=tok, chat_id=cid))
            log.info("Telegram output enabled → chat %s", cid)
        else:
            log.warning("--telegram set but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing")

    return channels


# ---------------------------------------------------------------------------
# Mode: digest — daily PR review digest
# ---------------------------------------------------------------------------

def build_digest_runtime(agent, schedule: str, repo: str, output_channels):
    from cuga_channels import CugaRuntime, CronChannel

    repo_note = f" for {repo}" if repo else ""
    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=(
                    f"Produce a daily pull request digest{repo_note}.\n\n"
                    "1. Call list_pull_requests to see all open PRs.\n"
                    "2. For any PR that looks significant, call get_pull_request "
                    "to check its review status.\n"
                    "3. Summarise: PRs waiting for review, PRs approved and ready to merge, "
                    "PRs with changes requested.\n"
                    "4. Flag any PR open more than 3 days.\n"
                    "Keep the digest under 400 words."
                ),
            )
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="github-digest",
    )


# ---------------------------------------------------------------------------
# Mode: CI/CD alert — webhook from GitHub Actions
# ---------------------------------------------------------------------------

def build_cicd_runtime(agent, port: int, output_channels):
    from cuga_channels import CugaRuntime, WebhookChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            WebhookChannel(
                port=port,
                path="/github",
                message_template=(
                    "A GitHub Actions event arrived:\n\n"
                    "{payload}\n\n"
                    "If this is a workflow failure:\n"
                    "1. Extract the repo, workflow name, branch, and run URL.\n"
                    "2. Call get_workflow_runs to confirm the failure and get context.\n"
                    "3. Write a concise alert: what failed, which branch, when.\n"
                    "4. Suggest likely cause and next steps.\n"
                    "If it is not a failure (success, queued, etc.), respond briefly.\n"
                ),
            )
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="github-cicd",
    )


# ---------------------------------------------------------------------------
# Mode: both
# ---------------------------------------------------------------------------

def build_both_runtime(agent, schedule: str, repo: str, port: int, output_channels):
    from cuga_channels import CugaRuntime, CronChannel, WebhookChannel

    repo_note = f" for {repo}" if repo else ""
    return CugaRuntime(
        agent=agent,
        input_channels=[
            CronChannel(
                schedule=schedule,
                message=(
                    f"Daily PR digest{repo_note}: call list_pull_requests, "
                    "summarise by review status, flag PRs open 3+ days."
                ),
            ),
            WebhookChannel(
                port=port,
                path="/github",
                message_template=(
                    "GitHub Actions event:\n{payload}\n\n"
                    "If a failure: call get_workflow_runs, write a brief alert "
                    "with cause and next steps."
                ),
            ),
        ],
        output_channels=output_channels,
        require_buffer=False,
        thread_id="github-both",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(mode: str, schedule: str, repo: str, port: int, now: bool,
              use_slack: bool, use_telegram: bool):
    agent   = make_agent(default_repo=repo)
    outputs = _make_output_channels(use_slack, use_telegram)

    if now:
        schedule = "* * * * *"

    if mode == "digest":
        runtime = build_digest_runtime(agent, schedule, repo, outputs)
        print(f"\n  GitHub Agent  —  PR Digest mode")
        print(f"  {'─' * 48}")
        print(f"  Repo     : {repo or '(from GITHUB_DEFAULT_REPO or tool arg)'}")
        print(f"  Schedule : {schedule}")
        if now:
            print(f"  (--now: firing every minute for testing)")
        print(f"  {'─' * 48}\n")

    elif mode == "cicd":
        runtime = build_cicd_runtime(agent, port, outputs)
        print(f"\n  GitHub Agent  —  CI/CD Alert mode")
        print(f"  {'─' * 48}")
        print(f"  Endpoint : http://localhost:{port}/github")
        print(f"  {'─' * 48}")
        print(f"\n  Test with a simulated failure:")
        print(f'    curl -X POST http://localhost:{port}/github \\')
        print(f'         -H "Content-Type: application/json" \\')
        print(f"         -d '{{\"action\":\"completed\",\"workflow_run\":{{\"name\":\"CI\",\"conclusion\":\"failure\",\"head_branch\":\"main\"}},\"repository\":{{\"full_name\":\"{repo or 'owner/repo'}\"}}}}'\n")

    else:  # both
        runtime = build_both_runtime(agent, schedule, repo, port, outputs)
        print(f"\n  GitHub Agent  —  Digest + CI/CD Alert mode")
        print(f"  {'─' * 48}")
        print(f"  Digest   : {schedule}")
        print(f"  Endpoint : http://localhost:{port}/github")
        if now:
            print(f"  (--now: digest fires every minute for testing)")
        print(f"  {'─' * 48}\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="GitHub Agent — PR digest and CI/CD alerts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--digest",    action="store_true",
                        help="PR digest mode: scheduled daily summary of open PRs")
    parser.add_argument("--cicd",      action="store_true",
                        help="CI/CD alert mode: receive GitHub Actions webhooks")
    parser.add_argument("--both",      action="store_true",
                        help="Both: digest + CI/CD alerts")
    parser.add_argument("--repo",      "-r",
                        default=os.getenv("GITHUB_DEFAULT_REPO", ""),
                        help="Repository in owner/repo format (or set GITHUB_DEFAULT_REPO)")
    parser.add_argument("--schedule",  "-s", default="0 9 * * 1-5",
                        help="Cron for digest mode (default: weekdays 9am)")
    parser.add_argument("--port",      type=int, default=18793,
                        help="Webhook port (default: 18793)")
    parser.add_argument("--now",       action="store_true",
                        help="Fire every minute instead of on schedule (for testing)")
    parser.add_argument("--slack",     action="store_true",
                        help="Deliver to Slack (needs SLACK_WEBHOOK_URL)")
    parser.add_argument("--telegram",  action="store_true",
                        help="Deliver to Telegram (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if not os.getenv("GITHUB_TOKEN"):
        print("\n  ERROR: GITHUB_TOKEN not set.")
        print("  github.com → Settings → Developer settings → Personal access tokens")
        print("  Scopes: repo (private) or public_repo (public)")
        print("  export GITHUB_TOKEN=ghp_...")
        print("\n  Or if you have gh CLI: export GITHUB_TOKEN=$(gh auth token)\n")
        return

    if args.digest:
        mode = "digest"
    elif args.cicd:
        mode = "cicd"
    elif args.both:
        mode = "both"
    else:
        mode = "digest"   # default: most useful for daily use

    asyncio.run(run(mode, args.schedule, args.repo, args.port, args.now,
                    args.slack, args.telegram))


if __name__ == "__main__":
    main()
