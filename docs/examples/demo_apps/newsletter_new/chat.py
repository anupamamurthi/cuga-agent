"""
Newsletter New — NL-driven pipeline setup.

Start the host first:
    cugahost start --factories newsletter_new.host_factories

Then run:
    python chat.py
    python chat.py --provider rits
    python chat.py "watch arxiv for AI agents, email me@x.com every morning"
"""
import asyncio
import os
import sys
from pathlib import Path

# Ensure the demos directory is on the path (for _llm helper)
sys.path.insert(0, str(Path(__file__).parent.parent))

from cuga_channels import CugaHostClient, CugaREPL, PipelineBuilder
from _llm import create_llm


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

    llm     = create_llm(provider=args.provider, model=args.model)
    client  = CugaHostClient(host_url=f"http://127.0.0.1:{args.port}")
    builder = PipelineBuilder(llm=llm, factory="newsletter_new")
    repl    = CugaREPL(
        client=client,
        builder=builder,
        app_name="Newsletter New",
        examples=[
            '"watch arxiv cs.AI for AI agent research, email me@example.com every morning"',
            '"monitor HuggingFace and VentureBeat for LLM news, daily digest at 8pm"',
            '"arxiv and hacker news, keywords: agent RAG, hourly digest to me@x.com"',
        ],
    )

    asyncio.run(repl.run(args.utterance))


if __name__ == "__main__":
    main()
