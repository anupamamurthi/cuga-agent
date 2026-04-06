"""
Image Chat — browser chat with image/PDF upload.

Architecture:
  Browser chat  →  ConversationGateway  →  CugaAgent
                                            └── analyze_image (docling)

  File upload:
    User attaches .png / .jpg / .pdf / .tiff
        →  POST /upload  (ConversationGateway built-in)
        →  Content prepended to chat message
        →  Agent calls analyze_image and returns structured report

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider rits

    Then open http://127.0.0.1:8770

Try saying:
    Upload an image, then ask: "What does this show?"
    Upload a PDF, then ask: "Extract the key fields"
    "What tables are in this document?"
    "Summarize the findings"

Required:
    pip install docling
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
    gateway.add_browser_adapter(host=host, port=port, title="Image Chat")
    await gateway.start()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Image Chat — browser chat with image/PDF analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider rits

            Then open http://127.0.0.1:8770

            Click 📎 to upload an image or PDF, then ask questions about it.
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8770)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Image Chat  →  http://{args.host}:{args.port}\n")
    print("  Tip: click 📎 to upload an image or PDF.\n")
    asyncio.run(main(args.host, args.port))
