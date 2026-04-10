"""
Second Brain — MCP + CugaAgent demo.

This is the reference use case for cuga-mcp.

What it demonstrates
--------------------
  MCPPlugin connects the official @modelcontextprotocol/server-filesystem
  MCP server to CugaAgent.  The agent gains read/write/list access to a
  local notes directory — no hand-written tool functions needed.

  The same pattern works for any MCP server:
    Gmail:  MCPServerConfig.sse("gmail", "http://localhost:3001/sse")
    Slack:  MCPServerConfig.stdio("slack", "npx",
                ["-y", "@modelcontextprotocol/server-slack"],
                env={"SLACK_BOT_TOKEN": "xoxb-..."})
    Box:    MCPServerConfig.sse("box", "http://localhost:3002/sse",
                headers={"Authorization": "Bearer BOX_TOKEN"})

Architecture
------------
  MCPPlugin
  └─ MCPServerConfig.stdio("filesystem", "npx",
         ["-y", "@modelcontextprotocol/server-filesystem", NOTES_DIR])
     └─ Tools registered: filesystem__read_file, filesystem__write_file,
                          filesystem__list_directory, filesystem__create_directory,
                          filesystem__move_file, filesystem__search_files, ...

  CugaAgent
  ├─ plugins: [MCPPlugin, CugaSkillsPlugin(skills/second_brain.md)]
  └─ All filesystem tools available via ReAct loop

  ConversationGateway
  └─ Browser chat at http://127.0.0.1:8768

Prerequisites
-------------
    npm (for npx — ships with Node.js)
    pip install cuga-mcp

Run
---
    python app.py --provider anthropic
    python app.py --provider openai --model gpt-4o
    python app.py --notes ~/my-notes       # custom notes directory
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_EXAMPLE_DIR  = Path(__file__).parent
_DEMOS_DIR    = _EXAMPLE_DIR.parent
_SKILLS_DIR   = _EXAMPLE_DIR / "skills"
_DEFAULT_NOTES = _EXAMPLE_DIR / "notes"

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("second_brain")


async def main(host: str, port: int, notes_dir: Path) -> None:
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from cuga_channels import ConversationGateway
    from cuga_mcp import MCPPlugin, MCPServerConfig
    from _llm import create_llm

    # ── 1. Ensure notes directory exists ────────────────────────────────────
    notes_dir.mkdir(parents=True, exist_ok=True)
    log.info("Notes directory: %s", notes_dir)

    # ── 2. Build MCPPlugin with the filesystem MCP server ───────────────────
    #
    # @modelcontextprotocol/server-filesystem exposes these tools:
    #   read_file, write_file, list_directory, create_directory,
    #   move_file, search_files, get_file_info
    #
    # MCPPlugin wraps them all automatically — no hand-written @tool functions.
    # They appear to CugaAgent as: filesystem__read_file, filesystem__write_file, ...
    #
    # Swap this config to use a different MCP server:
    #   Gmail:  MCPServerConfig.sse("gmail", "http://localhost:3001/sse")
    #   GitHub: MCPServerConfig.stdio("github", "npx",
    #               ["-y", "@modelcontextprotocol/server-github"],
    #               env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.getenv("GITHUB_TOKEN")})
    #
    mcp_plugin = MCPPlugin([
        MCPServerConfig.stdio(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", str(notes_dir)],
        )
    ])

    log.info("Connecting to filesystem MCP server…")
    await mcp_plugin.initialize()
    log.info("MCP tools registered: %s", mcp_plugin.tool_names)

    # ── 3. Build CugaAgent ───────────────────────────────────────────────────
    #
    # MCPPlugin provides the tools.
    # CugaSkillsPlugin provides the skills (how to use the tools).
    # Neither plugin knows about the other — they compose cleanly.
    #
    agent = CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        plugins=[
            mcp_plugin,
            CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR)),
        ],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )

    # ── 4. Start ConversationGateway ─────────────────────────────────────────
    gateway = ConversationGateway(agent=agent)
    gateway.add_browser_adapter(host=host, port=port, title="Second Brain")

    log.info("Second Brain → http://%s:%d", host, port)
    log.info("Notes stored in: %s", notes_dir)

    try:
        await gateway.start()
    finally:
        await mcp_plugin.aclose()


if __name__ == "__main__":
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        description="Second Brain — MCP filesystem + CugaAgent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py --provider anthropic
              python app.py --provider openai --model gpt-4o
              python app.py --notes ~/my-notes --provider anthropic

            Try these prompts:
              "Save this: LangGraph's Send API is the right primitive for parallel sub-agents"
              "What have I saved about LangGraph?"
              "Show me all my notes"
              "Add to my LangGraph note: also useful for fan-out patterns"
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8768)
    parser.add_argument("--notes",    default=str(_DEFAULT_NOTES),
        help="Directory for notes (created if absent)")
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Second Brain (MCP)  →  http://{args.host}:{args.port}")
    print(f"  Notes              →  {args.notes}\n")

    asyncio.run(main(
        host=args.host,
        port=args.port,
        notes_dir=Path(args.notes).expanduser().resolve(),
    ))
