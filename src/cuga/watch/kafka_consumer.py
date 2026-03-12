"""
KafkaWatchConsumer — subscribes to a Kafka topic and dispatches actions.

Architecture
------------
The consumer runs a blocking confluent-kafka poll loop in a ThreadPoolExecutor
so it stays asyncio-compatible.  Each message is deserialized and passed to
_dispatch_action (imported from executor.py) for every action in the config —
the exact same email / sms / log / agent_notify logic used by the classic
WatchExecutor.

The condition from the config is used for display purposes only (subject /
body formatting) — the producer has already done the filtering.

Usage
-----
    python -m cuga.watch.kafka_consumer watch_config_kafka.json

Or programmatically:

    from cuga.watch.kafka_models import KafkaWatchConfig
    from cuga.watch.kafka_consumer import KafkaWatchConsumer
    import asyncio, json

    config = KafkaWatchConfig(**json.load(open("watch_config_kafka.json")))
    asyncio.run(KafkaWatchConsumer(config).run())
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from cuga.watch.executor import _dispatch_action
from cuga.watch.kafka_models import KafkaWatchConfig


class KafkaWatchConsumer:
    """
    Consumes WatchEvent messages from a Kafka topic and dispatches actions
    defined in the WatchConfig.

    Parameters
    ----------
    config : KafkaWatchConfig
        Watch specification including Kafka connectivity.
    cuga_agent : optional
        CugaAgent instance for agent_notify actions.  Auto-created if any
        action has type="agent_notify" and this is not provided.
    thread_id : str, optional
        Stable thread ID passed to CugaAgent for agent_notify actions.
    """

    def __init__(
        self,
        config: KafkaWatchConfig,
        cuga_agent: Any = None,
        thread_id: str = "",
    ) -> None:
        self.config = config
        self._cuga_agent = cuga_agent
        self._thread_id = thread_id
        self._consumer: Any = None
        self._running = False

        if any(a.type == "agent_notify" for a in config.actions) and cuga_agent is None:
            try:
                from cuga.sdk import CugaAgent
                self._cuga_agent = CugaAgent(tools=[])
                logger.info("[kafka-consumer] Auto-created CugaAgent for agent_notify actions")
            except Exception as e:
                logger.warning(f"[kafka-consumer] Could not create CugaAgent: {e}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Subscribe to the topic and dispatch actions until cancelled."""
        self._consumer = self._make_consumer()
        self._consumer.subscribe([self.config.kafka.topic])
        self._running = True

        logger.info(
            f"[kafka-consumer] Starting — {self.config.description!r}\n"
            f"  Topic:    {self.config.kafka.topic}\n"
            f"  Group:    {self.config.kafka.group_id}\n"
            f"  Brokers:  {self.config.kafka.bootstrap_servers}\n"
            f"  Actions:  {[a.type for a in self.config.actions]}"
        )

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="kafka-poll") as executor:
            try:
                while self._running:
                    # Run the blocking poll in a thread so the event loop stays alive
                    msg = await loop.run_in_executor(executor, self._poll_one)
                    if msg is None:
                        continue
                    await self._handle_message(msg)
            except asyncio.CancelledError:
                logger.info("[kafka-consumer] Shutting down.")
            finally:
                self._running = False
                self._consumer.close()
                logger.info("[kafka-consumer] Consumer closed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_consumer(self) -> Any:
        try:
            from confluent_kafka import Consumer  # type: ignore
        except ImportError:
            raise RuntimeError(
                "confluent-kafka not installed. Run: pip install confluent-kafka"
            )
        cfg = self.config.kafka
        return Consumer({
            "bootstrap.servers": cfg.bootstrap_servers,
            "group.id": cfg.group_id,
            "auto.offset.reset": cfg.consumer_auto_offset_reset,
            "enable.auto.commit": cfg.consumer_enable_auto_commit,
        })

    def _poll_one(self) -> Any | None:
        """Blocking poll — returns a confluent_kafka.Message or None."""
        msg = self._consumer.poll(timeout=1.0)
        if msg is None:
            return None
        if msg.error():
            from confluent_kafka import KafkaError  # type: ignore
            if msg.error().code() == KafkaError._PARTITION_EOF:
                return None
            logger.error(f"[kafka-consumer] Kafka error: {msg.error()}")
            return None
        return msg

    async def _handle_message(self, msg: Any) -> None:
        """Deserialize a Kafka message and dispatch all configured actions."""
        try:
            event: dict = json.loads(msg.value().decode("utf-8"))
        except Exception as e:
            logger.warning(f"[kafka-consumer] Could not deserialize message: {e}")
            return

        matches: list[dict] = event.get("matches", [])
        if not matches:
            return

        # Prefer the thread_id embedded by the producer so agent memory is
        # scoped to the originating watch, not this consumer process.
        thread_id = (
            event.get("thread_id")
            or event.get("watch_id")
            or self._thread_id
            or self.config.effective_thread_id()
        )

        logger.info(
            f"[kafka-consumer] Received {len(matches)} match(es) from "
            f"{event.get('source_name', '?')} "
            f"(event_id={event.get('event_id', '?')} watch_id={event.get('watch_id', '?')} "
            f"thread_id={thread_id!r})"
        )

        await asyncio.gather(*(
            _dispatch_action(
                action,
                matches,
                self.config.condition,
                self._cuga_agent,
                thread_id=thread_id,
            )
            for action in self.config.actions
        ))


# ---------------------------------------------------------------------------
# CLI entry point:  python -m cuga.watch.kafka_consumer <config.json>
# ---------------------------------------------------------------------------

def _main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m cuga.watch.kafka_consumer <watch_config_kafka.json>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    config = KafkaWatchConfig(**raw)
    asyncio.run(KafkaWatchConsumer(config).run())


if __name__ == "__main__":
    _main()
