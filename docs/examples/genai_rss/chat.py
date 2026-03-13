"""
Gen AI RSS Watch — conversational interface
============================================
Chat with CUGA to start, stop, and manage the Gen AI RSS newsletter watch
using plain English.

CUGA knows about:
  - start_watch_from_file  — start a watch from a named config file
  - start_watch            — start a watch from raw JSON (for custom configs)
  - stop_watch             — stop a running watch by watch_id
  - list_watches           — list all active monitors

Example utterances
------------------
  "start the gen AI RSS watch"
  "start the gen AI kafka watch"
  "list my watches"
  "stop watch a3f2c1b0"
  "start monitoring arXiv and Hacker News every 4 hours for LLM news, email me"

Usage
-----
    source .venv/bin/activate
    python docs/examples/genai_rss/chat.py

    # Or with a one-shot command (no interactive loop):
    python docs/examples/genai_rss/chat.py "start the gen AI RSS watch"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger

from cuga.sdk import CugaAgent
from cuga.watch.tools import watch_tools

# Absolute paths to the pre-built configs — injected into the system prompt
# so CUGA knows what "the gen AI watch" refers to without spelling out the path.
_HERE = Path(__file__).parent.resolve()

# Direct configs  →  start_watch_from_file  (WatchExecutor, no Kafka)
_DIRECT_CONFIGS = {
    "gen AI RSS watch": _HERE / "watch_config_genai_rss.json",
}

# Kafka configs  →  start_kafka_watch_from_file  (KafkaProducer + KafkaConsumer)
_KAFKA_CONFIGS = {
    "gen AI kafka watch": _HERE / "watch_config_genai_rss_kafka.json",
}

_SYSTEM_HINT = (
    "You are a watch manager assistant for CUGA. "
    "You help the user start, stop, and monitor background RSS watchers.\n\n"
    "There are two watch modes:\n"
    "  1. Direct (WatchExecutor) — use start_watch_from_file. "
    "Single process: polls RSS → keyword filter → CugaAgent → email.\n"
    "  2. Kafka (Producer + Consumer) — use start_kafka_watch_from_file. "
    "Producer polls RSS and publishes matches to a Kafka topic; "
    "Consumer reads the topic and dispatches actions via CugaAgent. "
    "Requires a running Kafka broker (localhost:9092).\n\n"
    "Known configs:\n"
    + "\n".join(f'  - "{name}" (direct): {path}' for name, path in _DIRECT_CONFIGS.items())
    + "\n"
    + "\n".join(f'  - "{name}" (kafka): {path}' for name, path in _KAFKA_CONFIGS.items())
    + "\n\n"
    "When the user says 'start the gen AI RSS watch', use start_watch_from_file.\n"
    "When the user says 'start the gen AI kafka watch' or mentions Kafka, use start_kafka_watch_from_file.\n"
    "Always confirm with watch_id, thread_id, and mode after starting.\n"
    "Use stop_watch <watch_id> to stop any watch regardless of mode."
)


async def chat(utterance: str, thread_id: str = "genai-rss-chat") -> str:
    agent = CugaAgent(tools=watch_tools)
    result = await agent.invoke(
        f"{_SYSTEM_HINT}\n\nUser: {utterance}",
        thread_id=thread_id,
    )
    return result.answer


async def interactive_loop() -> None:
    print("CUGA Watch Chat — type your command, Ctrl+C to exit.\n")
    print("Known configs:")
    for name, path in _CONFIGS.items():
        print(f"  '{name}' → {path.name}")
    print()

    agent = CugaAgent(tools=watch_tools)
    thread_id = "genai-rss-chat"

    while True:
        try:
            utterance = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not utterance:
            continue

        full_prompt = f"{_SYSTEM_HINT}\n\nUser: {utterance}"
        try:
            result = await agent.invoke(full_prompt, thread_id=thread_id)
            print(f"CUGA: {result.answer}\n")
        except Exception as e:
            logger.error(f"Agent error: {e}")
            print(f"Error: {e}\n")


async def main() -> None:
    if len(sys.argv) > 1:
        # One-shot mode: python chat.py "start the gen AI RSS watch"
        utterance = " ".join(sys.argv[1:])
        print(f"You: {utterance}")
        answer = await chat(utterance)
        print(f"CUGA: {answer}")
    else:
        await interactive_loop()


if __name__ == "__main__":
    asyncio.run(main())
