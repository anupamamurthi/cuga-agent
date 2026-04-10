"""
Video Q&A New — universal app registration model.

Two modes in one app:

  DIRECT (on-demand Q&A):
    "transcribe /path/to/meeting.mp4"
    "what was said about the Q2 budget in standup.mp4?"
    "what was discussed at the 10-minute mark in meeting.mp4?"

  PIPELINE (folder watching):
    "watch /recordings for IBM stock mentions, email me with timestamps"
    "every morning summarise new videos from /uploads and email team@company.com"
    "monitor /meetings — whenever AI strategy is discussed, alert me@x.com"

Start CugaHost first (once, stays running):
    cugahost start

Then run this chat client:
    python chat.py
    python chat.py --provider anthropic
    python chat.py "transcribe /path/to/meeting.mp4"
    python chat.py "watch /recordings for IBM mentions, email me@x.com"
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

    parser = argparse.ArgumentParser(description="Video Q&A — folder watching + on-demand Q&A")
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
    client = CugaHostClient(host_url=f"http://127.0.0.1:{args.port}", timeout=600)

    if not await client.is_running():
        print(
            "\nCugaHost is not running.\n"
            "\nStart it first:\n"
            "  cugahost start\n"
            "\nThen re-run this script.\n"
        )
        import sys; sys.exit(1)

    # Register this app with the universal CugaHost (idempotent).
    await client.register_app(
        app_id      = "video-qa",
        agent       = "agent:make_agent",
        skills_dir  = str(_HERE / "skills"),
        description = "Video Q&A — folder watching with timestamps + on-demand Q&A",
        provider    = args.provider,
        model       = args.model,
    )

    repl = CugaREPL(
        client   = client,
        app_id   = "video-qa",
        app_name = "Video Q&A",
        examples = [
            # Direct Q&A
            '"transcribe /path/to/meeting.mp4"',
            '"what was said about the Q2 budget in meeting.mp4?"',
            '"what was discussed at the 10-minute mark in standup.mp4?"',
            '"summarise the key decisions from meeting.mp4"',
            # Pipeline / folder watching
            '"watch /recordings for IBM stock mentions, email me@x.com with timestamps"',
            '"monitor /meetings — if AI strategy discussed, alert cto@company.com"',
            '"every morning summarise new videos from /uploads, email team@company.com"',
            # Management
            '"list my pipelines"',
            '"stop the recordings watcher"',
        ],
    )

    await repl.run(args.utterance)


if __name__ == "__main__":
    main()
