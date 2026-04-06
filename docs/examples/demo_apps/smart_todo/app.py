"""
Smart Todo — OpenClaw-style personal assistant.

The app's only job:
  1. Build the agent (with all tools including config tools)
  2. Hand it to ConversationGateway (handles the UI + routing)
  3. Start CugaHost (owns the background digest pipeline)
  4. Start CugaWatcher (fires reminders when due)

The user talks to the agent naturally — todos, reminders, notes,
and pipeline configuration ("send my digest at 9am") all go through
the same conversation interface.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from store import init_db
init_db()


async def main(host: str, port: int) -> None:
    from cuga_channels import ConversationGateway, CugaHostClient
    from agent import make_agent, make_watcher

    # 1. Connect to CugaHost (or start embedded) — owns digest pipeline.
    cuga_host, client = await CugaHostClient.connect_or_embed(
        state_dir=_EXAMPLE_DIR / ".cuga" / "host",
        pipelines_config=_EXAMPLE_DIR / "cuga_pipelines.yaml",
    )

    # 2. Register digest runtime if not already persisted.
    await client.ensure_runtime("smart-todo-digest", "digest", {
        "schedule": os.getenv("DIGEST_SCHEDULE", "0 8 * * 1-5"),
        "email":    os.getenv("DIGEST_TO"),
    })

    # 3. Build the one agent — data tools + config tools (OpenClaw model).
    agent = make_agent(client=client)

    # 4. Start the reminder watcher.
    watcher = make_watcher(agent)
    asyncio.create_task(watcher.start())

    # 5. Start the conversation gateway — this is the entire UI + routing.
    gateway = ConversationGateway(agent=agent)
    gateway.add_browser_adapter(host=host, port=port, title="Smart Todo")

    try:
        await gateway.start()
    finally:
        await watcher.stop()
        if cuga_host is not None:
            await cuga_host.stop()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Smart Todo — OpenClaw-style personal assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py --provider rits
              python app.py --provider anthropic
              python app.py --provider openai --model gpt-4o
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8765)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Smart Todo  →  http://{args.host}:{args.port}\n")
    asyncio.run(main(args.host, args.port))
