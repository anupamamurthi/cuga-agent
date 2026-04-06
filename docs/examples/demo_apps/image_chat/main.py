"""
Screenshot Analyst — cuga++ image intelligence pipeline
=========================================================

Two trigger sources. One agent. Zero manual invocation.

  📁 Drop Folder   (CugaWatcher)
     Drop any image or PDF into ./inbox/ — the agent fires automatically,
     extracts the content with docling, and appends an analysis to report.md.

  🌐 HTTP Webhook  (CugaRuntime + WebhookChannel)
     POST {"file_path": "...", "question": "..."} to /analyze — the same
     agent handles it and smart_deliver() routes the answer.

Both triggers share one CugaAgent. Both write to the same report.

─────────────────────────────────────────────────────────────────────────────
What cuga++ gives you that core cuga does not
─────────────────────────────────────────────────────────────────────────────

Without cuga++, to replicate this you would need to write yourself:

  1.  A polling loop with asyncio.create_task() + sleep — to watch the folder
  2.  Deduplication logic — track which files you've already processed
  3.  An aiohttp HTTP server — to handle webhook POSTs
  4.  asyncio.gather() wiring — to run both the poller and the server
  5.  Output routing code — to decide where to send each result
  6.  Error isolation — so one failing file doesn't kill the whole loop

  That's ~100-150 lines of plumbing before you write a single line of
  agent logic, and adding a third trigger (e.g. email attachment, RSS feed)
  requires restructuring the whole app.

With cuga++:

  • Each trigger source is a channel  — one object, 1-3 config lines
  • Channels are composable          — add/remove without touching agent code
  • Output routing is one function   — smart_deliver()
  • The concurrency model is hidden  — asyncio.gather() is inside the framework
  • Adding a third source tomorrow   — just add it to the list

─────────────────────────────────────────────────────────────────────────────

Run:
    python main.py
    python main.py --provider anthropic
    python main.py --port 18791 --watch ./inbox

Drop a file:
    cp ~/Desktop/screenshot.png ./inbox/

Test the webhook:
    curl -X POST http://localhost:18791/analyze \\
         -H "Content-Type: application/json" \\
         -d '{"file_path": "/path/to/image.png", "question": "What is shown?"}'

Environment variables:
    LLM_PROVIDER    rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL       model override
    WATCH_DIR       folder to watch  (default: ./inbox)
    REPORT_FILE     output report    (default: ./report.md)
    WEBHOOK_PORT    port for webhook (default: 18791)
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

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp"}


# ---------------------------------------------------------------------------
# Tool: docling image/PDF extraction
#
# This is the agent's only capability.  The skill file (skills/analyst.md)
# tells the agent when and how to call it.
# ---------------------------------------------------------------------------

def make_tools():
    from langchain_core.tools import tool

    @tool
    def analyze_image(file_path: str) -> str:
        """
        Extract the full text and structure from an image or PDF using docling.
        Use this whenever you receive a file path to an image or document.

        Handles: PNG, JPEG, TIFF, BMP (OCR) and PDF (layout-aware extraction).
        Returns clean markdown preserving tables, headings, and code blocks.

        Args:
            file_path: Absolute or relative path to the image or PDF file.
        """
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            return (
                "Error: docling is not installed.\n"
                "Run: pip install docling"
            )

        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return f"Error: file not found: {path}"

        try:
            converter = DocumentConverter()
            result    = converter.convert(str(path))
            markdown  = result.document.export_to_markdown()
        except Exception as e:
            return f"Error during docling extraction: {e}"

        if not markdown.strip():
            return "(docling extracted no text — image may be purely graphical)"

        return markdown

    return [analyze_image]


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
        tools=make_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
        auto_load_policies=False,
    )


# ---------------------------------------------------------------------------
# Shared output writer
# ---------------------------------------------------------------------------

def write_report(report_file: Path, filename: str, analysis: str):
    """Append one analysis entry to the persistent markdown report."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n---\n\n"
        f"## {filename} — {timestamp}\n\n"
        f"{analysis}\n"
    )
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(entry)

    print(f"\n{'═' * 64}")
    print(f"  {filename}  ({timestamp})")
    print(f"{'═' * 64}")
    print(analysis)
    print()


# ---------------------------------------------------------------------------
# Trigger 1: Drop Folder  (CugaWatcher)
#
# The developer writes:  what to watch  +  what to do when files appear.
# The framework handles: polling interval, concurrency, error isolation.
#
# Without cuga++:
#   while True:
#       files = [f for f in inbox.iterdir() if ...]
#       for f in files:
#           shutil.move(f, processed/f)
#           result = await agent.invoke(...)
#           write_report(...)
#       await asyncio.sleep(poll_seconds)
#   — plus you'd need asyncio.create_task() to run this alongside the
#     webhook server, deduplication, and error handling per file.
# ---------------------------------------------------------------------------

def build_watcher(agent, watch_dir: Path, report_file: Path, poll_seconds: int):
    from cuga_watcher import CugaWatcher

    watcher       = CugaWatcher(agent=agent)
    processed_dir = watch_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    @watcher.source(every_minutes=poll_seconds / 60, name="new_images")
    async def scan_inbox() -> list[Path]:
        files = [
            f for f in watch_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if files:
            log.info("Drop folder: found %d new file(s): %s",
                     len(files), [f.name for f in files])
        return files

    @watcher.on(scan_inbox, when=lambda files: len(files) > 0)
    async def handle_dropped_files(files: list[Path]):
        for file_path in files:
            # Move before invoking — prevents double-processing if agent is slow
            dest = processed_dir / file_path.name
            try:
                shutil.move(str(file_path), str(dest))
            except Exception as e:
                log.warning("Could not move %s: %s", file_path.name, e)
                continue

            log.info("Analyzing (drop folder): %s", file_path.name)
            result = await agent.invoke(
                f"A new file has been dropped: {dest}\n\n"
                f"Analyze it using the analyze_image tool and return a "
                f"structured intelligence report.",
                thread_id=f"analyst-{file_path.stem}",
            )
            write_report(report_file, file_path.name, result.answer)

    return watcher


# ---------------------------------------------------------------------------
# Custom OutputChannel: writes agent answers to the shared report file.
#
# OutputChannel protocol requires only two things: a `name` str attribute
# and an async `deliver(content, metadata)` method.  No base class needed.
# This is how you extend cuga++ output routing without touching the runtime.
# ---------------------------------------------------------------------------

class ReportFileChannel:
    """OutputChannel that appends each agent answer to a markdown report file."""

    name = "report-file"

    def __init__(self, report_file: Path):
        self._report_file = report_file

    async def deliver(self, content: str, _metadata: dict) -> None:
        ts       = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"webhook-{ts}"
        write_report(self._report_file, filename, content)


# ---------------------------------------------------------------------------
# Trigger 2: HTTP Webhook  (CugaRuntime + WebhookChannel)
#
# The developer writes:  port, path, and message template.
# The framework handles: HTTP server, request parsing, response routing.
#
# Without cuga++:
#   from aiohttp import web
#   async def handle(request):
#       body = await request.json()
#       result = await agent.invoke(...)
#       return web.Response(text=result.answer)
#   app = web.Application()
#   app.router.add_post("/analyze", handle)
#   runner = web.AppRunner(app)
#   await runner.setup()
#   site = web.TCPSite(runner, "0.0.0.0", port)
#   await site.start()
#   — then you'd need to run this and the watcher concurrently.
# ---------------------------------------------------------------------------

def build_runtime(agent, report_file: Path, port: int):
    from cuga_channels import CugaRuntime, WebhookChannel, LogChannel

    return CugaRuntime(
        agent=agent,
        input_channels=[
            WebhookChannel(
                port=port,
                path="/analyze",
                message_template=(
                    "An image analysis request was received via webhook.\n\n"
                    "Request payload:\n{payload}\n\n"
                    "Parse the file_path from the payload, call analyze_image "
                    "on it, then answer the question (if any) or return a full "
                    "intelligence report."
                ),
            )
        ],
        output_channels=[
            LogChannel(),
            ReportFileChannel(report_file),
        ],
        require_buffer=False,
        thread_id="analyst-webhook",
    )


# ---------------------------------------------------------------------------
# Main — run both triggers concurrently
#
# This is the payoff: one agent, two live trigger sources, zero manual wiring.
# Adding a third source (CronChannel, IMAPChannel, RssChannel) is one more
# channel in the list — the agent code doesn't change at all.
# ---------------------------------------------------------------------------

async def run(watch_dir: Path, report_file: Path, poll_seconds: int, port: int):
    agent   = make_agent()
    watcher = build_watcher(agent, watch_dir, report_file, poll_seconds)
    runtime = build_runtime(agent, report_file, port)

    print(f"\n  Screenshot Analyst  —  cuga++ pipeline")
    print(f"  {'─' * 44}")
    print(f"  Drop folder  : {watch_dir}  (poll every {poll_seconds}s)")
    print(f"  Webhook      : http://localhost:{port}/analyze")
    print(f"  Report       : {report_file}")
    print(f"  {'─' * 44}")
    print(f"\n  Two triggers, one agent.  Waiting for events...\n")
    print(f"  Drop an image:  cp ~/Desktop/screenshot.png {watch_dir}/")
    print(f"  Send a webhook:")
    print(f'    curl -X POST http://localhost:{port}/analyze \\')
    print(f'         -H "Content-Type: application/json" \\')
    print(f'         -d \'{{"file_path": "/path/to/image.png", "question": "What is shown?"}}\'\n')

    # asyncio.gather runs both concurrently — the framework handles the rest.
    try:
        await asyncio.gather(
            watcher.start(),
            runtime.launch(),
        )
    except (SystemExit, OSError) as e:
        if "address already in use" in str(e).lower() or (isinstance(e, OSError) and e.errno == 48):
            print(
                f"\n  ❌  Port {port} is already in use.\n"
                f"     Kill any previous instance:  pkill -f 'image_chat/main.py'\n"
                f"     Or use a different port:      python main.py --port 18792\n",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Screenshot Analyst — cuga++ image intelligence pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider", "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    parser.add_argument("--watch",         default=os.getenv("WATCH_DIR",   str(_DIR / "inbox")))
    parser.add_argument("--output",        default=os.getenv("REPORT_FILE", str(_DIR / "report.md")))
    parser.add_argument("--interval", "-i", type=int,
                        default=int(os.getenv("POLL_SECONDS", "15")))
    parser.add_argument("--port",          type=int,
                        default=int(os.getenv("WEBHOOK_PORT", "18791")))
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    watch_dir   = Path(args.watch)
    report_file = Path(args.output)
    watch_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(run(watch_dir, report_file, args.interval, args.port))


if __name__ == "__main__":
    main()
