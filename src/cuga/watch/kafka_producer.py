"""
KafkaWatchProducer — polls sources, filters by condition, publishes matches.

Architecture
------------
One asyncio task is created per source in the config.  Each task polls its
source on the configured interval, runs _filter_matches, and publishes any
matches as a JSON message to the configured Kafka topic.

Message envelope (JSON)
-----------------------
{
    "event_id":   "<uuid4>",
    "timestamp":  "<ISO-8601>",
    "source_name": "<str>",
    "source_url":  "<str>",
    "matches": [
        {
            "text":             "<str>",
            "url":              "<str>",
            "source_name":      "<str>",
            "matched_keywords": ["<str>", ...]
        },
        ...
    ]
}

Usage
-----
    python -m cuga.watch.kafka_producer watch_config_kafka.json

Or programmatically:

    from cuga.watch.kafka_models import KafkaWatchConfig
    from cuga.watch.kafka_producer import KafkaWatchProducer
    import asyncio, json

    config = KafkaWatchConfig(**json.load(open("watch_config_kafka.json")))
    asyncio.run(KafkaWatchProducer(config).run())
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from cuga.watch.executor import _fetch_source, _filter_matches
from cuga.watch.kafka_models import KafkaWatchConfig
from cuga.watch.models import WatchSource


class KafkaWatchProducer:
    """
    Polls WatchConfig sources on their configured intervals and publishes
    filtered matches to a Kafka topic.

    Parameters
    ----------
    config : KafkaWatchConfig
        Watch specification including Kafka connectivity.
    """

    def __init__(self, config: KafkaWatchConfig) -> None:
        self.config = config
        self._producer: Any = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all per-source polling tasks and block until cancelled."""
        self._producer = self._make_producer()

        kws = self.config.condition.keywords
        logger.info(
            f"[kafka-producer] Starting — {self.config.description!r}\n"
            f"  Sources:  {[s.name or s.url for s in self.config.sources]}\n"
            f"  Condition: {self.config.condition.type}"
            + (f" → {kws}" if kws else "")
            + f"\n  Topic:    {self.config.kafka.topic}\n"
            f"  Brokers:  {self.config.kafka.bootstrap_servers}"
        )

        tasks = [
            asyncio.create_task(self._poll_loop(source), name=f"producer-{source.name or source.type}")
            for source in self.config.sources
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("[kafka-producer] Shutting down.")
        finally:
            if self._producer is not None:
                self._producer.flush()
                logger.info("[kafka-producer] Producer flushed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_producer(self) -> Any:
        try:
            from confluent_kafka import Producer  # type: ignore
        except ImportError:
            raise RuntimeError(
                "confluent-kafka not installed. Run: pip install confluent-kafka"
            )
        cfg = self.config.kafka
        return Producer({
            "bootstrap.servers": cfg.bootstrap_servers,
            "acks": cfg.producer_acks,
            "retries": cfg.producer_retries,
        })

    async def _poll_loop(self, source: WatchSource) -> None:
        """Poll a single source forever, publishing matches each cycle."""
        interval_secs = source.interval_minutes * 60
        first = True
        while True:
            if not first:
                await asyncio.sleep(interval_secs)
            first = False

            try:
                items = await _fetch_source(source)
                matches = _filter_matches(items, self.config.condition)
                if matches:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._publish, matches, source
                    )
                else:
                    logger.debug(
                        f"[kafka-producer] No matches from {source.name or source.url}"
                    )
            except Exception as e:
                logger.exception(
                    f"[kafka-producer] Error polling {source.name or source.url}: {e}"
                )

    def _publish(self, matches: list[dict], source: WatchSource) -> None:
        """Serialize and produce a message for a batch of matches (blocking)."""
        event: dict = {
            "event_id": uuid.uuid4().hex,
            "watch_id": self.config.watch_id,
            "thread_id": self.config.effective_thread_id(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_name": source.name or source.url,
            "source_url": source.url,
            "matches": matches,
        }
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        topic = self.config.kafka.topic
        key = (source.name or source.url).encode("utf-8")

        self._producer.produce(
            topic,
            value=payload,
            key=key,
            on_delivery=self._on_delivery,
        )
        self._producer.poll(0)  # trigger delivery callbacks without blocking
        logger.info(
            f"[kafka-producer] Published {len(matches)} match(es) from "
            f"{source.name or source.url!r} → topic {topic!r}"
        )

    @staticmethod
    def _on_delivery(err: Any, msg: Any) -> None:
        if err:
            logger.error(f"[kafka-producer] Delivery failed: {err}")
        else:
            logger.debug(
                f"[kafka-producer] Delivered to {msg.topic()}[{msg.partition()}] "
                f"offset {msg.offset()}"
            )


# ---------------------------------------------------------------------------
# CLI entry point:  python -m cuga.watch.kafka_producer <config.json>
# ---------------------------------------------------------------------------

def _main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m cuga.watch.kafka_producer <watch_config_kafka.json>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    config = KafkaWatchConfig(**raw)
    asyncio.run(KafkaWatchProducer(config).run())


if __name__ == "__main__":
    _main()
