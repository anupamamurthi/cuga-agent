"""
Gen AI RSS Watch — Kafka runner
================================
Runs the Kafka producer and consumer for the Gen AI RSS watch in a single
process (convenient for local dev).  In production you would run them as
separate processes — possibly on separate machines.

Architecture
------------

  ┌─────────────────────────────────────────────────────────────────────┐
  │                       Producer (this process)                       │
  │                                                                     │
  │   arXiv cs.AI  ──┐                                                  │
  │   arXiv cs.CL  ──┤                                                  │
  │   HuggingFace  ──┼──► _fetch_source ──► keyword filter ──► Kafka   │
  │   Hacker News  ──┤        (every N min per feed)          topic     │
  │   VentureBeat  ──┤                                                  │
  │   Reddit ML    ──┘                                                  │
  └─────────────────────────────────────────────────────────────────────┘
                                    │
                         cuga.watch.events (Kafka topic)
                                    │
  ┌─────────────────────────────────▼───────────────────────────────────┐
  │                       Consumer (this process)                       │
  │                                                                     │
  │   Kafka msg ──► CugaAgent (newsletter editor)                       │
  │                    • reads matched items                            │
  │                    • judges significance                            │
  │                    • writes HTML newsletter                         │
  │                    • calls send_newsletter_email ──► 📧             │
  └─────────────────────────────────────────────────────────────────────┘

Separate-process usage (production)
------------------------------------
    # Terminal 1 — producer
    python -m cuga.watch.kafka_producer docs/examples/genai_rss/watch_config_genai_rss_kafka.json

    # Terminal 2 — consumer
    python -m cuga.watch.kafka_consumer docs/examples/genai_rss/watch_config_genai_rss_kafka.json

Single-process usage (local dev)
---------------------------------
    python docs/examples/genai_rss/run_kafka.py

Prerequisites
-------------
    Docker:  docker run -d --name kafka -p 9092:9092 apache/kafka:3.7.0
    Package: pip install confluent-kafka
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from cuga.sdk import CugaAgent
from cuga.watch.executor import make_send_email_tool
from cuga.watch.kafka_consumer import KafkaWatchConsumer
from cuga.watch.kafka_models import KafkaWatchConfig
from cuga.watch.kafka_producer import KafkaWatchProducer


async def main() -> None:
    config_path = Path(__file__).parent / "watch_config_genai_rss_kafka.json"
    config = KafkaWatchConfig(**json.loads(config_path.read_text()))

    # Producer needs no CugaAgent — it only polls RSS and publishes to Kafka.
    producer = KafkaWatchProducer(config)

    # Consumer gets a CugaAgent with the send_newsletter_email tool so it can
    # compose and deliver the HTML newsletter after reading matched items.
    agent_notify_actions = [a for a in config.actions if a.type == "agent_notify"]
    email_tools = [make_send_email_tool(a) for a in agent_notify_actions if a.smtp_username]
    agent = CugaAgent(tools=email_tools)
    consumer = KafkaWatchConsumer(
        config,
        cuga_agent=agent,
        thread_id=config.effective_thread_id(),
    )

    logger.info(
        "[genai-rss-kafka] Starting producer + consumer\n"
        f"  Feeds:   {[s.name for s in config.sources]}\n"
        f"  Topic:   {config.kafka.topic}\n"
        f"  Brokers: {config.kafka.bootstrap_servers}\n"
        f"  Action:  CUGA agent_notify → HTML newsletter email"
    )

    # Run producer and consumer concurrently in the same event loop.
    await asyncio.gather(
        producer.run(),
        consumer.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
