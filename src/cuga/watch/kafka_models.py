"""
Kafka-specific models for cuga watch.

KafkaWatchConfig extends WatchConfig with a `kafka` block — everything else
(sources, condition, actions) is identical to the existing schema so the same
watch_config.json can drive either the classic WatchExecutor or the Kafka
producer/consumer pair.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from cuga.watch.models import WatchConfig


class KafkaConfig(BaseModel):
    """Confluent Kafka connection + topic settings."""

    bootstrap_servers: str = "localhost:9092"
    """Comma-separated list of broker host:port pairs."""

    topic: str = "cuga.watch.events"
    """Topic that the producer writes to and the consumer reads from."""

    group_id: str = "cuga-watch-consumers"
    """Consumer group ID (only used by the consumer)."""

    # Producer tuning (sensible defaults for local dev)
    producer_acks: str = "all"
    producer_retries: int = 3

    # Consumer tuning
    consumer_auto_offset_reset: str = "earliest"
    consumer_enable_auto_commit: bool = True


class KafkaWatchConfig(WatchConfig):
    """
    WatchConfig + Kafka connectivity.

    Drop-in replacement for WatchConfig.  The `kafka` field is the only
    addition; all existing fields (sources, condition, actions, archive_*)
    are inherited unchanged.

    Session identity
    ----------------
    watch_id  — stable identifier for this watch run, embedded in every
                Kafka message so the consumer can distinguish events from
                different watches on the same topic.

    thread_id — stable CugaAgent conversation thread ID, also embedded in
                every Kafka message.  The consumer uses the per-message
                thread_id (not a process-level one) so agent memory is
                scoped to the watch that produced the event, not the
                consumer process.  Survives consumer restarts as long as
                the config is reused unchanged.
    """

    kafka: KafkaConfig = Field(default_factory=KafkaConfig)

    watch_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    """Stable ID for this watch.  Set explicitly to preserve identity across restarts."""

    thread_id: str = ""
    """
    Stable CugaAgent thread ID for agent_notify actions.
    Defaults to 'watch-<watch_id>' at runtime if left empty.
    """

    def effective_thread_id(self) -> str:
        """Return thread_id, falling back to 'watch-<watch_id>'."""
        return self.thread_id or f"watch-{self.watch_id}"
