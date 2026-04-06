"""
Drop Summarizer — browser chat with file upload.

Architecture:
  Browser chat  →  ConversationGateway  →  CugaAgent (no extra tools)
                                            └── skills/summarizer.md

  File upload:
    User attaches any .txt / .md / .pdf / .csv
        →  POST /upload  (ConversationGateway built-in)
        →  Text content prepended to chat message
        →  Agent summarizes and responds

  The agent's built-in skills handle summarization —
  no custom tools needed for text-only summarization.

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider rits

    Then open http://127.0.0.1:8772

Try:
    Upload a .txt or .pdf file, then say "summarize this"
    "What are the key points?"
    "Give me a one-paragraph summary"
    "Extract the action items"
"""
from __future__ import annotations

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
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


async def main(host: str, port: int) -> None:
    from cuga_channels import ConversationGateway
    from main import make_agent

    agent   = make_agent()
    gateway = ConversationGateway(agent=agent)
    gateway.add_browser_adapter(host=host, port=port, title="Drop Summarizer")
    await gateway.start()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Drop Summarizer — browser chat with file summarization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider rits

            Then open http://127.0.0.1:8772

            Click 📎 to upload a .txt, .md, .pdf, or .csv file.
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8772)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Drop Summarizer  →  http://{args.host}:{args.port}\n")
    print("  Tip: click 📎 to upload a document and ask for a summary.\n")
    asyncio.run(main(args.host, args.port))
