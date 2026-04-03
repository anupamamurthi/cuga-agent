#!/usr/bin/env python3
"""
Newsletter Monitor
==================
Monitors RSS feeds and delivers a curated HTML digest on a schedule.

cuga++ handles everything except reasoning:
  RssChannel   — polls feeds, keyword-filters, buffers items (no LLM)
  CronChannel  — fires every N hours
  CugaRuntime  — injects buffer into trigger message, calls agent, routes output
  EmailChannel — delivers agent output (or LogChannel for testing)

CugaAgent handles reasoning only:
  receives buffered items + task description → produces HTML newsletter
  no send_email tool, no read_buffer tool — cuga++ owns the pipeline

Usage
-----
  python monitor.py
  python monitor.py --poll 15 --digest 240
  python monitor.py --provider anthropic
  python monitor.py --log          # print digest to stdout instead of emailing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cuga import CugaAgent
from cuga_channels import CronChannel, CugaRuntime, EmailChannel, LogChannel, RssChannel
from cuga_skills import CugaSkillsPlugin
from _llm import create_llm

_SKILLS_DIR = _EXAMPLE_DIR / "skills"

_DEFAULT_SOURCES = [
    {"name": "arXiv cs.AI",      "url": "https://arxiv.org/rss/cs.AI"},
    {"name": "arXiv cs.LG",      "url": "https://arxiv.org/rss/cs.LG"},
    {"name": "HuggingFace Blog", "url": "https://huggingface.co/blog/feed.xml"},
    {"name": "HN — AI/LLM",      "url": "https://hnrss.org/newest?q=LLM+AI+agent&points=10"},
    {"name": "VentureBeat AI",   "url": "https://venturebeat.com/category/ai/feed/"},
]
_DEFAULT_KEYWORDS = [
    "CUGA", "ALTK", "LLM", "large language model",
    "agent", "agentic", "RAG", "reasoning",
    "Claude", "GPT", "Gemini", "Llama",
]
_DIGEST_MESSAGE = textwrap.dedent("""\
    It's time to send the newsletter digest.

    You have been given a list of RSS items collected since the last digest.
    Select the most significant and diverse items, avoiding duplicates from prior runs.
    Compose a styled HTML newsletter using the newsletter_curation skill.

    Your full output will be delivered automatically — do not call any send or email tools.
""").strip()


def _interval_to_cron(minutes: int) -> str:
    if minutes < 60:
        return f"*/{minutes} * * * *"
    return f"0 */{minutes // 60} * * *"


def build(
    provider: str | None = None,
    model: str | None = None,
    poll_minutes: int = 15,
    digest_minutes: int = 240,
    log_only: bool = False,
) -> CugaRuntime:
    cfg_path = _EXAMPLE_DIR / "config.json"
    cfg      = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    sources  = cfg.get("sources", _DEFAULT_SOURCES)
    keywords = cfg.get("keywords", _DEFAULT_KEYWORDS)
    llm_cfg  = cfg.get("llm", {})

    agent = CugaAgent(
        model=create_llm(
            provider=provider or llm_cfg.get("provider"),
            model=model or llm_cfg.get("model"),
        ),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )

    smtp_ready = bool(os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))
    output_channels = (
        [LogChannel()]
        if log_only or not smtp_ready
        else [EmailChannel.from_env(), LogChannel()]
    )

    return CugaRuntime(
        agent=agent,
        input_channels=[
            RssChannel(sources=sources, keywords=keywords, poll_minutes=poll_minutes),
            CronChannel(
                schedule=_interval_to_cron(digest_minutes),
                message=_DIGEST_MESSAGE,
                name="digest-cron",
            ),
        ],
        output_channels=output_channels,
        thread_id="newsletter-digest",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Newsletter monitor — channel-based RSS digest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python monitor.py
              python monitor.py --poll 15 --digest 60
              python monitor.py --provider anthropic --log
        """),
    )
    parser.add_argument("--config", "-c", default=str(_EXAMPLE_DIR / "config.json"))
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--poll", type=int, default=15,
        help="RSS polling interval in minutes (default: 15)")
    parser.add_argument("--digest", type=int, default=240,
        help="Digest dispatch interval in minutes (default: 240 = every 4h)")
    parser.add_argument("--log", action="store_true",
        help="Print digest to stdout instead of sending email")
    args = parser.parse_args()

    runtime = build(args.provider, args.model, args.poll, args.digest, args.log)
    asyncio.run(runtime.start())


if __name__ == "__main__":
    main()
