"""
Voice Journal — browser chat with audio/file upload.

Architecture (OpenClaw model):

  Browser chat  →  ConversationGateway  →  CugaAgent
                                            ├── save_journal_entry
                                            ├── list_entries / list_dates
                                            └── skills/journal_reasoning.md

  File upload:
    User attaches .m4a / .mp3 / .wav / PDF / .txt
        →  POST /upload
        →  AudioChannel.transcribe() for audio
        →  pypdf for PDF
        →  decoded text for everything else
        →  prepended to the next chat message
        →  agent structures and saves as journal entry

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider openai --model gpt-4o
    open http://127.0.0.1:8766

Required env vars:
    One of: ANTHROPIC_API_KEY, OPENAI_API_KEY, RITS_API_KEY, etc.

Optional (for audio transcription without OpenAI):
    pip install openai-whisper    # local Whisper — no API key needed

Optional (for audio via cloud):
    OPENAI_API_KEY                # used for OpenAI Whisper API fallback
    RITS_API_KEY                  # used for RITS Whisper API fallback
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_DIR = Path(__file__).parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))
if str(_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_DIR.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from store import init_db
init_db()


async def main(host: str, port: int) -> None:
    from cuga_channels import ConversationGateway
    from agent import make_agent

    agent   = make_agent()
    gateway = ConversationGateway(agent=agent)
    gateway.add_browser_adapter(host=host, port=port, title="Voice Journal")
    await gateway.start()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Voice Journal — OpenClaw-style personal journal with audio upload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider openai --model gpt-4o
              python app.py --provider ollama         # local, no API key

            Then open http://127.0.0.1:8766

            Supported uploads (click 📎 in the chat):
              Audio:  .mp3  .m4a  .wav  .ogg  .flac  .webm
              Docs:   .pdf  .txt  .md   .csv  any text file
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8766)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Voice Journal  →  http://{args.host}:{args.port}\n")
    print("  Tip: click 📎 in the chat to upload a voice note or document.\n")
    asyncio.run(main(args.host, args.port))
