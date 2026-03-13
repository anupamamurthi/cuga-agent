"""
CUGA Watch Tools — expose watch management as CugaAgent @tool functions.

Level 1: The agent can start, stop, and list background monitors via tool calls.
         The user can say "Watch the Chappaqua Moms group for nanny posts and email
         me" and the agent will plan, parse, and launch the watcher itself.

Level 2: Matches feed back into the same CugaAgent on a stable thread_id, giving
         the agent persistent memory across events so it can reason across matches
         (e.g. "this is the 3rd nanny post this week").
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from langchain_core.tools import tool
from loguru import logger


# ---------------------------------------------------------------------------
# WatchManager — registry of running watch tasks
# ---------------------------------------------------------------------------

class WatchManager:
    """
    Singleton registry of running WatchExecutor background tasks.

    Tracks watch_id → {description, asyncio.Task, thread_id, status}.
    """

    _instance: WatchManager | None = None

    def __new__(cls) -> WatchManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._watches: dict[str, dict[str, Any]] = {}
        return cls._instance

    def register(self, watch_id: str, description: str, task: asyncio.Task, thread_id: str) -> None:
        self._watches[watch_id] = {
            "id": watch_id,
            "description": description,
            "thread_id": thread_id,
            "task": task,
            "status": "running",
        }

    def cancel(self, watch_id: str) -> bool:
        entry = self._watches.get(watch_id)
        if not entry:
            return False
        entry["task"].cancel()
        entry["cancelled"] = True
        return True

    def _entry_status(self, w: dict[str, Any]) -> str:
        if w.get("cancelled"):
            return "stopped"
        return "running" if not w["task"].done() else "done"

    def status(self) -> list[dict[str, Any]]:
        # Prune entries whose tasks are fully done to avoid unbounded growth
        done_ids = [wid for wid, w in self._watches.items() if w["task"].done()]
        for wid in done_ids:
            self._watches.pop(wid)
        return [
            {
                "id": w["id"],
                "description": w["description"],
                "thread_id": w["thread_id"],
                "status": self._entry_status(w),
            }
            for w in self._watches.values()
        ]


# Module-level singleton
_manager = WatchManager()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
async def start_watch(config_json: str, thread_id: str = "") -> str:
    """
    Start a background event monitor from a WatchConfig JSON string.

    The monitor polls sources on a schedule, filters by condition, and dispatches
    actions (email, sms, log, agent_notify) when keyword matches are found.

    When thread_id is supplied, agent_notify actions feed matches back into the
    CugaAgent on that thread, giving the agent persistent memory across events.

    Args:
        config_json: JSON string conforming to the WatchConfig schema. Must include
                     sources (type, url, interval_minutes), condition (type, keywords),
                     and actions (type: email|sms|log|agent_notify).
        thread_id:   Optional stable conversation thread ID. If omitted, a new ID
                     is generated. Reuse the same thread_id across restarts to
                     preserve the agent's event memory.

    Returns:
        A summary including the watch_id (use with stop_watch) and thread_id.
    """
    from cuga.watch.models import WatchConfig
    from cuga.watch.executor import WatchExecutor

    try:
        raw = json.loads(config_json)
        config = WatchConfig(**raw)
    except Exception as e:
        return f"Error: could not parse WatchConfig — {e}"

    watch_id = uuid.uuid4().hex[:8]
    effective_thread_id = thread_id or f"watch-{watch_id}"

    executor = WatchExecutor(config, thread_id=effective_thread_id)
    task = asyncio.create_task(
        executor.run(),
        name=f"watch-{watch_id}",
    )
    _manager.register(watch_id, config.description, task, effective_thread_id)

    logger.info(
        f"[watch_tools] Started watch {watch_id!r}: {config.description!r} "
        f"(thread: {effective_thread_id!r})"
    )
    return (
        f"Watch started.\n"
        f"  watch_id:  {watch_id}\n"
        f"  thread_id: {effective_thread_id}\n"
        f"  sources:   {[s.name or s.url for s in config.sources]}\n"
        f"  condition: {config.condition.type} {config.condition.keywords or ''}\n"
        f"  actions:   {[a.type for a in config.actions]}"
    )


@tool
def stop_watch(watch_id: str) -> str:
    """
    Stop a running background event monitor.

    Args:
        watch_id: The ID returned by start_watch.

    Returns:
        Confirmation message.
    """
    if _manager.cancel(watch_id):
        return f"Watch {watch_id!r} stopped."
    return f"No running watch found with id {watch_id!r}. Use list_watches to see active monitors."


@tool
def list_watches() -> str:
    """
    List all background event monitors and their current status.

    Returns:
        A formatted summary of all watches including watch_id, thread_id, and status.
    """
    entries = _manager.status()
    if not entries:
        return "No watches registered."
    lines = ["Active watches:"]
    for w in entries:
        lines.append(
            f"  [{w['status']}] {w['id']} "
            f"(thread: {w['thread_id']}) — {w['description']}"
        )
    return "\n".join(lines)


@tool
async def start_watch_from_file(config_path: str, thread_id: str = "") -> str:
    """
    Start a background event monitor by loading a WatchConfig from a JSON file.

    Identical to start_watch but accepts a file path instead of raw JSON —
    convenient when a named config already exists on disk.

    Args:
        config_path: Absolute or relative path to a watch_config*.json file.
        thread_id:   Optional stable conversation thread ID for agent_notify
                     actions.  Reuse across restarts to preserve agent memory.

    Returns:
        A summary including the watch_id (use with stop_watch) and thread_id.
    """
    import json as _json
    from pathlib import Path
    from cuga.watch.models import WatchConfig
    from cuga.watch.executor import WatchExecutor

    try:
        raw = _json.loads(Path(config_path).read_text())
        config = WatchConfig(**raw)
    except FileNotFoundError:
        return f"Error: file not found — {config_path}"
    except Exception as e:
        return f"Error: could not load WatchConfig from {config_path} — {e}"

    watch_id = uuid.uuid4().hex[:8]
    effective_thread_id = thread_id or f"watch-{watch_id}"

    # WatchExecutor auto-injects send_newsletter_email for agent_notify actions
    # that have SMTP creds — no manual wiring needed here.
    executor = WatchExecutor(config, thread_id=effective_thread_id)
    task = asyncio.create_task(
        executor.run(),
        name=f"watch-{watch_id}",
    )
    _manager.register(watch_id, config.description, task, effective_thread_id)

    logger.info(
        f"[watch_tools] Started watch {watch_id!r} from {config_path!r}: "
        f"{config.description!r} (thread: {effective_thread_id!r})"
    )
    return (
        f"Watch started from {config_path}.\n"
        f"  watch_id:  {watch_id}\n"
        f"  thread_id: {effective_thread_id}\n"
        f"  sources:   {[s.name or s.url for s in config.sources]}\n"
        f"  condition: {config.condition.type} {config.condition.keywords[:4] or ''}"
        + (f" … (+{len(config.condition.keywords) - 4} more)" if len(config.condition.keywords) > 4 else "")
        + f"\n  actions:   {[a.type for a in config.actions]}"
    )


@tool
async def start_kafka_watch_from_file(config_path: str, thread_id: str = "") -> str:
    """
    Start a Kafka-backed event monitor (producer + consumer) from a KafkaWatchConfig
    JSON file.

    The producer polls the configured sources on a schedule and publishes keyword
    matches to a Kafka topic.  The consumer reads from that topic and dispatches
    actions (email, sms, log, agent_notify).  Both run as concurrent background
    tasks in the same process.

    Requires a running Kafka broker (default: localhost:9092).
    Install: pip install confluent-kafka
    Quick start: docker run -d --name kafka -p 9092:9092 apache/kafka:3.7.0

    Args:
        config_path: Path to a watch_config*_kafka.json file (KafkaWatchConfig schema).
        thread_id:   Optional stable CugaAgent thread ID for agent_notify actions.

    Returns:
        A summary including the watch_id (use with stop_watch) and topic info.
    """
    import json as _json
    from pathlib import Path
    from cuga.watch.kafka_models import KafkaWatchConfig
    from cuga.watch.kafka_producer import KafkaWatchProducer
    from cuga.watch.kafka_consumer import KafkaWatchConsumer
    from cuga.watch.executor import make_send_email_tool
    from cuga.sdk import CugaAgent

    try:
        raw = _json.loads(Path(config_path).read_text())
        config = KafkaWatchConfig(**raw)
    except FileNotFoundError:
        return f"Error: file not found — {config_path}"
    except Exception as e:
        return f"Error: could not load KafkaWatchConfig from {config_path} — {e}"

    watch_id = uuid.uuid4().hex[:8]
    effective_thread_id = thread_id or config.effective_thread_id()

    # Producer: polls sources, filters, publishes to Kafka topic. No agent needed.
    producer = KafkaWatchProducer(config)

    # Consumer: reads from Kafka topic, dispatches actions via CugaAgent.
    # Inject send_newsletter_email for agent_notify actions that carry SMTP creds.
    try:
        email_tools = [
            make_send_email_tool(a)
            for a in config.actions
            if a.type == "agent_notify" and (a.smtp_username or __import__("os").environ.get("WATCH_EMAIL_USERNAME"))
        ]
        agent = CugaAgent(tools=email_tools)
        consumer = KafkaWatchConsumer(config, cuga_agent=agent, thread_id=effective_thread_id)
    except Exception as e:
        return f"Error: could not create CugaAgent for consumer — {e}"

    async def _run_both() -> None:
        await asyncio.gather(producer.run(), consumer.run())

    task = asyncio.create_task(_run_both(), name=f"kafka-watch-{watch_id}")
    _manager.register(
        watch_id,
        f"[Kafka] {config.description}",
        task,
        effective_thread_id,
    )

    logger.info(
        f"[watch_tools] Started Kafka watch {watch_id!r} from {config_path!r} "
        f"(thread: {effective_thread_id!r}, topic: {config.kafka.topic!r})"
    )
    return (
        f"Kafka watch started from {config_path}.\n"
        f"  watch_id:  {watch_id}\n"
        f"  thread_id: {effective_thread_id}\n"
        f"  topic:     {config.kafka.topic}\n"
        f"  brokers:   {config.kafka.bootstrap_servers}\n"
        f"  sources:   {[s.name or s.url for s in config.sources]}\n"
        f"  actions:   {[a.type for a in config.actions]}"
    )


@tool
async def start_kafka_watch(
    config_json: str,
    bootstrap_servers: str = "localhost:9092",
    topic: str = "cuga.watch.events",
    group_id: str = "cuga-watch-consumers",
    thread_id: str = "",
) -> str:
    """
    Start a Kafka-backed event monitor (producer + consumer) from a JSON string
    or a natural-language instruction.

    Accepts either:
    - A WatchConfig JSON string (sources, condition, actions) — Kafka defaults are
      applied unless overridden via the bootstrap_servers / topic / group_id params.
    - A KafkaWatchConfig JSON string (already contains a "kafka" block).
    - A natural-language instruction (e.g. "monitor arXiv for LLM papers, email me
      hourly via Kafka") — parsed automatically into a WatchConfig via
      WatchInstructionParser.

    The producer polls sources and publishes keyword matches to the Kafka topic.
    The consumer reads the topic and dispatches actions (email, sms, log,
    agent_notify).  Both run as concurrent background tasks.

    Requires a running Kafka broker.
    Quick start: docker run -d --name kafka -p 9092:9092 apache/kafka:3.7.0

    Args:
        config_json:       WatchConfig JSON, KafkaWatchConfig JSON, or a
                           natural-language monitoring instruction.
        bootstrap_servers: Kafka broker(s), e.g. "localhost:9092". Ignored if
                           config_json already contains a "kafka.bootstrap_servers".
        topic:             Kafka topic name. Ignored if already set in config_json.
        group_id:          Consumer group ID. Ignored if already set in config_json.
        thread_id:         Stable CugaAgent thread ID for agent_notify actions.
                           Reuse across restarts to preserve agent memory.

    Returns:
        A summary including watch_id, thread_id, topic, and active sources.
    """
    import os
    from cuga.watch.kafka_models import KafkaWatchConfig, KafkaConfig
    from cuga.watch.kafka_producer import KafkaWatchProducer
    from cuga.watch.kafka_consumer import KafkaWatchConsumer
    from cuga.watch.executor import make_send_email_tool
    from cuga.sdk import CugaAgent

    # ------------------------------------------------------------------
    # 1. Parse config_json — try JSON first, fall back to NL parser
    # ------------------------------------------------------------------
    raw: dict | None = None
    try:
        raw = json.loads(config_json)
    except (json.JSONDecodeError, ValueError):
        # Not valid JSON — treat as a natural-language instruction
        try:
            from cuga.watch.parser import WatchInstructionParser
            watch_config = WatchInstructionParser().parse(config_json)
            raw = watch_config.model_dump()
        except Exception as e:
            return f"Error: could not parse instruction as JSON or natural language — {e}"

    # ------------------------------------------------------------------
    # 2. Ensure a "kafka" block exists, applying caller overrides where
    #    the config doesn't already specify values.
    # ------------------------------------------------------------------
    if "kafka" not in raw:
        raw["kafka"] = {}
    kafka_block = raw["kafka"]
    if not kafka_block.get("bootstrap_servers"):
        kafka_block["bootstrap_servers"] = bootstrap_servers
    if not kafka_block.get("topic"):
        kafka_block["topic"] = topic
    if not kafka_block.get("group_id"):
        kafka_block["group_id"] = group_id

    try:
        config = KafkaWatchConfig(**raw)
    except Exception as e:
        return f"Error: invalid config — {e}"

    watch_id = uuid.uuid4().hex[:8]
    effective_thread_id = thread_id or config.effective_thread_id()

    # ------------------------------------------------------------------
    # 3. Build producer (no agent) + consumer (with CugaAgent for
    #    agent_notify actions that carry SMTP credentials)
    # ------------------------------------------------------------------
    producer = KafkaWatchProducer(config)

    try:
        email_tools = [
            make_send_email_tool(a)
            for a in config.actions
            if a.type == "agent_notify" and (a.smtp_username or os.environ.get("WATCH_EMAIL_USERNAME"))
        ]
        agent = CugaAgent(tools=email_tools)
        consumer = KafkaWatchConsumer(config, cuga_agent=agent, thread_id=effective_thread_id)
    except Exception as e:
        return f"Error: could not create CugaAgent for consumer — {e}"

    async def _run_both() -> None:
        await asyncio.gather(producer.run(), consumer.run())

    task = asyncio.create_task(_run_both(), name=f"kafka-watch-{watch_id}")
    _manager.register(
        watch_id,
        f"[Kafka] {config.description}",
        task,
        effective_thread_id,
    )

    logger.info(
        f"[watch_tools] Started Kafka watch {watch_id!r} "
        f"(thread: {effective_thread_id!r}, topic: {config.kafka.topic!r})"
    )
    return (
        f"Kafka watch started.\n"
        f"  watch_id:  {watch_id}\n"
        f"  thread_id: {effective_thread_id}\n"
        f"  topic:     {config.kafka.topic}\n"
        f"  brokers:   {config.kafka.bootstrap_servers}\n"
        f"  sources:   {[s.name or s.url for s in config.sources]}\n"
        f"  condition: {config.condition.type} {config.condition.keywords[:4] or ''}"
        + (f" … (+{len(config.condition.keywords) - 4} more)" if len(config.condition.keywords) > 4 else "")
        + f"\n  actions:   {[a.type for a in config.actions]}"
    )


# Convenience list — pass directly to CugaAgent(tools=watch_tools)
watch_tools = [start_watch, start_watch_from_file, start_kafka_watch, start_kafka_watch_from_file, stop_watch, list_watches]
