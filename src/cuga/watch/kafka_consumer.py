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

from cuga.watch.executor import _dispatch_action, make_send_email_tool
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

        # Batched dispatch buffer: accumulate matches across sources, drain once per interval.
        self._dispatch_buffer: list[dict] = []
        self._dispatch_lock = asyncio.Lock()
        self._last_thread_id: str = ""

        if any(a.type == "agent_notify" for a in config.actions) and cuga_agent is None:
            try:
                import os
                from cuga.sdk import CugaAgent
                email_tools = [
                    make_send_email_tool(a)
                    for a in config.actions
                    if a.type == "agent_notify" and (a.smtp_username or os.environ.get("WATCH_EMAIL_USERNAME"))
                ]
                self._cuga_agent = CugaAgent(tools=email_tools)
                logger.info(
                    f"[kafka-consumer] Auto-created CugaAgent for agent_notify "
                    f"({'with' if email_tools else 'without'} send_newsletter_email tool)"
                )
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

        dispatch_interval = self.config.dispatch_interval_minutes
        logger.info(
            f"[kafka-consumer] Starting — {self.config.description!r}\n"
            f"  Topic:    {self.config.kafka.topic}\n"
            f"  Group:    {self.config.kafka.group_id}\n"
            f"  Brokers:  {self.config.kafka.bootstrap_servers}\n"
            f"  Actions:  {[a.type for a in self.config.actions]}\n"
            f"  Dispatch: {'batched every ' + str(dispatch_interval) + ' min' if dispatch_interval > 0 else 'immediate'}"
        )

        loop = asyncio.get_event_loop()
        tasks = []
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="kafka-poll") as executor:
            try:
                poll_task = asyncio.create_task(self._poll_loop(loop, executor))
                tasks.append(poll_task)
                if dispatch_interval > 0:
                    drain_task = asyncio.create_task(self._drain_loop(dispatch_interval))
                    tasks.append(drain_task)
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("[kafka-consumer] Shutting down.")
            finally:
                self._running = False
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._consumer.close()
                logger.info("[kafka-consumer] Consumer closed.")

    async def _poll_loop(self, loop: Any, executor: Any) -> None:
        """Blocking poll loop — runs until self._running is False."""
        while self._running:
            msg = await loop.run_in_executor(executor, self._poll_one)
            if msg is None:
                continue
            await self._handle_message(msg)

    async def _drain_loop(self, interval_minutes: float) -> None:
        """Periodically drain the dispatch buffer and send ONE consolidated newsletter."""
        interval_seconds = interval_minutes * 60
        await asyncio.sleep(interval_seconds)  # first drain after one full interval
        while self._running:
            await self._drain_buffer()
            await asyncio.sleep(interval_seconds)

    async def _drain_buffer(self) -> None:
        """Flush accumulated matches as a single newsletter dispatch."""
        async with self._dispatch_lock:
            if not self._dispatch_buffer:
                return
            batch = list(self._dispatch_buffer)
            thread_id = self._last_thread_id
            self._dispatch_buffer.clear()

        logger.info(f"[kafka-consumer] Draining {len(batch)} buffered match(es) as one newsletter")
        await asyncio.gather(*(
            _dispatch_action(
                action,
                batch,
                self.config.condition,
                self._cuga_agent,
                thread_id=thread_id,
            )
            for action in self.config.actions
        ))

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
        """Deserialize a Kafka message and either buffer or dispatch immediately."""
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

        if self.config.dispatch_interval_minutes > 0:
            # Batched mode: buffer matches and let _drain_loop send ONE newsletter.
            async with self._dispatch_lock:
                self._dispatch_buffer.extend(matches)
                self._last_thread_id = thread_id
            logger.debug(
                f"[kafka-consumer] Buffered {len(matches)} match(es) "
                f"(buffer size: {len(self._dispatch_buffer)})"
            )
        else:
            # Immediate mode: dispatch as soon as each message arrives.
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
