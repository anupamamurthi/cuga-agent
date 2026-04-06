"""
Document Intelligence — browser chat with PDF/image upload.

Architecture:
  Browser chat  →  ConversationGateway  →  CugaAgent
                                            ├── extract_document  (structured fields)
                                            ├── enrich_document   (summary + keywords)
                                            └── query_document    (RAG Q&A)

  File upload:
    User attaches .pdf / .png / .jpg / .tiff
        →  POST /upload  (ConversationGateway built-in)
        →  Text content prepended to chat message
        →  Agent picks the right docling tool automatically

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider rits

    Then open http://127.0.0.1:8773

Try:
    Upload an invoice PDF → "Extract all fields"
    Upload a research paper → "Summarize the key findings"
    Upload any PDF → "What are the main conclusions?"
    "Extract the table on page 2"

Required:
    pip install docling docling-agent
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
    gateway.add_browser_adapter(host=host, port=port, title="Doc Intelligence")
    await gateway.start()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Document Intelligence — browser chat with docling tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider rits

            Then open http://127.0.0.1:8773

            Click 📎 to upload a PDF or image. The agent will:
              • Invoices / CVs / forms  → extract structured fields
              • Reports / articles      → enrich with summary and keywords
              • Any PDF + question      → answer via RAG
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8773)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Doc Intelligence  →  http://{args.host}:{args.port}\n")
    print("  Tip: click 📎 to upload a PDF or image.\n")
    asyncio.run(main(args.host, args.port))
