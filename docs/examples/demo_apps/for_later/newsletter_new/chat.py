"""
Newsletter New — universal app registration model.

CugaHost is the always-alive process.  This script registers the newsletter app
and starts a conversational REPL.  The CugaHost router handles everything:
  - "watch arxiv hourly, email me daily"  → starts a pipeline automatically
  - "what's the latest AI news?"          → answers directly via agent
  - "list my pipelines"                   → shows active pipelines
  - "stop the arxiv pipeline"             → stops it

Start CugaHost first (once, stays running):
    cugahost start

Then run this chat client:
    python chat.py
    python chat.py --provider anthropic
    python chat.py "watch arxiv for AI agents, email me@example.com every morning"
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cuga_channels import CugaHostClient, CugaREPL

_HERE = Path(__file__).parent


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Newsletter — NL-driven pipeline setup")
    parser.add_argument("utterance", nargs="?", default=None,
        help="One-shot utterance. Omit for interactive REPL.")
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--port",  type=int, default=18790)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    asyncio.run(_run(args))


async def _run(args) -> None:
    client = CugaHostClient(host_url=f"http://127.0.0.1:{args.port}")

    if not await client.is_running():
        print(
            "\nCugaHost is not running.\n"
            "\nStart it first:\n"
            "  cugahost start\n"
            "\nThen re-run this script.\n"
        )
        import sys; sys.exit(1)

    # Register this app with the universal CugaHost.
    # Idempotent — safe to call on every startup.
    await client.register_app(
        app_id      = "newsletter",
        agent       = "agent:make_agent",
        skills_dir  = str(_HERE / "skills"),
        description = "Newsletter curation — watches RSS feeds and sends digests",
        provider    = args.provider,
        model       = args.model,
    )

    repl = CugaREPL(
        client   = client,
        app_id   = "newsletter",
        app_name = "Newsletter",
        examples = [
            '"watch arxiv cs.AI for AI agent research, email me@example.com every morning"',
            '"monitor HuggingFace and VentureBeat for LLM news, daily digest at 8pm"',
            '"arxiv and hacker news, keywords: agent RAG, hourly digest to me@x.com"',
            '"what are the key AI trends this week?"',
            '"list my pipelines"',
            '"stop the arxiv pipeline"',
        ],
    )

    await repl.run(args.utterance)


if __name__ == "__main__":
    main()
