"""
Document Pipeline — DoclingChannel demo
=========================================

Demonstrates DoclingChannel as a proper DataChannel:

  1.  Drop any PDF / image / Office doc into ./inbox/
  2.  DoclingChannel detects the file, runs docling extraction,
      and pushes pre-extracted markdown into the runtime buffer.
  3.  CronChannel fires the agent every poll_minutes.
  4.  The agent receives clean text — no tool call, no "remember to extract" prompt.
  5.  Analysis is written to report.md and logged to stdout.

─────────────────────────────────────────────────────────────────────────────
Why DoclingChannel beats the tool-based approach
─────────────────────────────────────────────────────────────────────────────

Tool-based (old image_chat style):
  • Agent decides when to call analyze_image() — can forget or skip.
  • Tool failure → agent gets an error string → bad output.
  • LLM token cost: every invocation includes the "call the tool" reasoning.

DoclingChannel (this demo):
  • Extraction is deterministic, pre-agent — no LLM decision required.
  • Agent receives clean text — just reasons, no tool plumbing.
  • Failure is surfaced as a clear "[DoclingChannel] Extraction failed" prefix.
  • Adding a new file type just means adding its extension to DoclingChannel.

─────────────────────────────────────────────────────────────────────────────

Run:
    python main.py
    python main.py --provider anthropic
    python main.py --poll 1 --watch ./inbox

Drop a file:
    cp ~/Desktop/invoice.pdf ./inbox/

Environment variables:
    LLM_PROVIDER    rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL       model override
    WATCH_DIR       folder to watch       (default: ./inbox)
    REPORT_FILE     output report file    (default: ./report.md)
    POLL_MINUTES    poll interval         (default: 1)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
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
# Report output channel
# ---------------------------------------------------------------------------

class ReportFileChannel:
    """OutputChannel — appends each agent analysis to report.md."""

    name = "report-file"

    def __init__(self, report_file: Path):
        self._report_file = report_file

    async def deliver(self, content: str, _metadata: dict) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n---\n\n## Run — {ts}\n\n{content}\n"
        with open(self._report_file, "a", encoding="utf-8") as f:
            f.write(entry)

        print(f"\n{'═' * 64}")
        print(f"  Analysis complete  ({ts})")
        print(f"{'═' * 64}")
        print(content)
        print()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    # No tools — DoclingChannel pre-extracts everything.
    # The agent just reasons over clean text.
    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=[],
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_runtime(agent, watch_dir: Path, report_file: Path, poll_minutes: float):
    from cuga_channels import CugaRuntime, DoclingChannel, CronChannel, LogChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            # DataChannel: extracts files → pushes to buffer
            DoclingChannel(
                watch_dir=watch_dir,
                poll_minutes=poll_minutes,
            ),
            # TriggerChannel: fires agent when buffer has items
            CronChannel(
                schedule=f"*/{max(1, int(poll_minutes))} * * * *",
                message=(
                    "New documents have been pre-extracted and are waiting in the buffer.\n\n"
                    "Each item has: file_name, content (docling markdown), extension, size_bytes.\n\n"
                    "Produce a structured intelligence report for each document."
                ),
            ),
        ],
        output_channels=[
            LogChannel(),
            ReportFileChannel(report_file),
        ],
        require_buffer=True,   # only fire when DoclingChannel has items
        thread_id="doc-pipeline",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(watch_dir: Path, report_file: Path, poll_minutes: float):
    agent   = make_agent()
    runtime = build_runtime(agent, watch_dir, report_file, poll_minutes)

    print(f"\n  Document Pipeline  —  DoclingChannel demo")
    print(f"  {'─' * 44}")
    print(f"  Watch folder : {watch_dir}")
    print(f"  Poll interval: every {poll_minutes} minute(s)")
    print(f"  Report file  : {report_file}")
    print(f"  {'─' * 44}")
    print(f"\n  No tools needed — DoclingChannel pre-extracts before the agent fires.")
    print(f"\n  Drop a file to trigger analysis:")
    print(f"    cp ~/Desktop/invoice.pdf {watch_dir}/")
    print(f"    cp ~/Desktop/cv.pdf {watch_dir}/")
    print(f"    cp ~/Desktop/receipt.png {watch_dir}/\n")

    await runtime.start()


def main():
    parser = argparse.ArgumentParser(
        description="Document Pipeline — DoclingChannel demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider", "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    parser.add_argument("--watch",         default=os.getenv("WATCH_DIR",    str(_DIR / "inbox")))
    parser.add_argument("--output",        default=os.getenv("REPORT_FILE",  str(_DIR / "report.md")))
    parser.add_argument("--poll",    "-i", type=float,
                        default=float(os.getenv("POLL_MINUTES", "1")))
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    watch_dir   = Path(args.watch)
    report_file = Path(args.output)
    watch_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(run(watch_dir, report_file, args.poll))


if __name__ == "__main__":
    main()
