"""
Web Researcher — browser chat interface.

Architecture:
  Browser chat  →  ConversationGateway  →  CugaAgent
                                            └── web_search (Tavily)

Run:
    python app.py
    python app.py --provider rits
    python app.py --provider anthropic

    Then open http://127.0.0.1:8774

Try saying:
    "Research the latest LLM benchmarks"
    "What's new in AI agents this week?"
    "Compare Claude 4 and GPT-4o — what are the key differences?"
    "Find recent papers on multimodal reasoning"

Required:
    pip install tavily-python
    export TAVILY_API_KEY=tvly-...
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
    gateway.add_browser_adapter(host=host, port=port, title="Web Researcher")
    await gateway.start()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Web Researcher — browser chat with Tavily web search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider rits
              python app.py --provider anthropic

            Then open http://127.0.0.1:8774

            Requires: TAVILY_API_KEY  (free at https://app.tavily.com)
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8774)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if not os.getenv("TAVILY_API_KEY"):
        print("\n  ⚠️  TAVILY_API_KEY not set — web search will fail.")
        print("  Get a free key at https://app.tavily.com\n")

    print(f"\n  Web Researcher  →  http://{args.host}:{args.port}\n")
    asyncio.run(main(args.host, args.port))
