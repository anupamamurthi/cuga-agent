"""
Drop Folder Summarizer — cuga++ demo
======================================

CugaWatcher polls a local folder every 30 seconds.
When new .txt or .md files appear, the agent summarizes each one
and appends an entry to summary_log.md.

The original file is moved to processed/ so it's never summarized twice.

Run:
    python main.py
    python main.py --provider anthropic
    python main.py --watch ./inbox --output ./summaries.md
    python main.py --interval 10    # poll every 10 seconds

Then drop any .txt or .md file into the watch folder and watch it get
summarized in real time.

Environment variables:
    LLM_PROVIDER    rits | anthropic | openai | watsonx | litellm | ollama
    LLM_MODEL       model override
    WATCH_DIR       folder to watch (default: ./inbox)
    OUTPUT_FILE     summary log file (default: ./summary_log.md)
    POLL_SECONDS    polling interval in seconds (default: 30)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
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

SUPPORTED_EXTENSIONS = {".txt", ".md"}


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
        tools=[],
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
    )


# ---------------------------------------------------------------------------
# Source: scan folder for new files
# ---------------------------------------------------------------------------

def make_scanner(watch_dir: Path):
    processed_dir = watch_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    async def scan_for_new_files() -> list[Path]:
        files = [
            f for f in watch_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return files

    return scan_for_new_files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(watch_dir: Path, output_file: Path, poll_seconds: int):
    from cuga_watcher import CugaWatcher

    agent = make_agent()
    watcher = CugaWatcher(agent=agent)
    processed_dir = watch_dir / "processed"

    scan_for_new_files = make_scanner(watch_dir)

    @watcher.source(every_minutes=poll_seconds / 60, name="new_files")
    async def check_folder() -> list[Path]:
        files = await scan_for_new_files()
        if files:
            log.info("Found %d new file(s): %s", len(files), [f.name for f in files])
        return files

    @watcher.on(check_folder, when=lambda files: len(files) > 0)
    async def summarize_files(files: list[Path]):
        for file_path in files:
            # Move first — prevents double-processing if agent is slow
            dest = processed_dir / file_path.name
            try:
                shutil.move(str(file_path), str(dest))
            except Exception as e:
                log.warning("Could not move %s: %s", file_path.name, e)
                continue

            log.info("Summarizing: %s", file_path.name)
            try:
                content = dest.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                log.error("Could not read %s: %s", file_path.name, e)
                continue

            result = await agent.invoke(
                f"Summarize the following document.\n\nFilename: {file_path.name}\n\n{content}",
                thread_id=f"summarize-{file_path.stem}",
            )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = (
                f"\n---\n\n"
                f"## {file_path.name} — {timestamp}\n\n"
                f"{result.answer}\n"
            )

            with open(output_file, "a", encoding="utf-8") as f:
                f.write(entry)

            print(f"\n{'─' * 60}")
            print(f"  {file_path.name}  ({timestamp})")
            print(f"{'─' * 60}")
            print(result.answer)
            print()

    print(f"\n  Drop Summarizer")
    print(f"  Watching : {watch_dir}")
    print(f"  Log      : {output_file}")
    print(f"  Interval : every {poll_seconds}s")
    print(f"\n  Drop any .txt or .md file into the watch folder.\n")

    await watcher.start()


def main():
    parser = argparse.ArgumentParser(description="Drop Folder Summarizer — cuga++ demo")
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--watch",          default=os.getenv("WATCH_DIR",   str(_DIR / "inbox")))
    parser.add_argument("--output",         default=os.getenv("OUTPUT_FILE", str(_DIR / "summary_log.md")))
    parser.add_argument("--interval",  "-i", type=int,
                        default=int(os.getenv("POLL_SECONDS", "30")))
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    watch_dir   = Path(args.watch)
    output_file = Path(args.output)
    watch_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(run(watch_dir, output_file, args.interval))


if __name__ == "__main__":
    main()
