"""
CugaWatcher — Proactive event-driven scheduling for CugaAgent.

While CugaAgent is reactive (responds to user input), CugaWatcher makes it
proactive: it watches external sources on a schedule and invokes the agent
only when a condition is met.

Conceptual model
----------------
  [source]  runs every N minutes, produces data
      ↓  emitted into internal queue
  [handler] receives data; optional `when` predicate filters before calling

Quick start
-----------
    from cuga import CugaAgent, CugaWatcher
    from langchain_core.tools import tool

    @tool
    def send_email(subject: str, body: str) -> str:
        '''Send an email notification'''
        ...

    agent = CugaAgent(tools=[send_email])
    watcher = CugaWatcher(agent)

    @watcher.source(every_minutes=30)
    async def fetch_data():
        return get_latest_items()          # return None/[] to emit nothing

    @watcher.on(fetch_data, when=lambda items: len(items) > 0)
    async def handle_new_items(items):
        await watcher.agent.invoke(f"Process these items and notify me: {items}")

    await watcher.start()   # runs forever; Ctrl-C to stop
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional
from loguru import logger

from cuga.sdk import CugaAgent


# ---------------------------------------------------------------------------
# Internal registration records
# ---------------------------------------------------------------------------

@dataclass
class _SourceReg:
    name: str
    fn: Callable[[], Coroutine[Any, Any, Any]]
    every_seconds: float
    run_immediately: bool = True   # run once on startup before the first sleep


@dataclass
class _HandlerReg:
    source_name: str
    fn: Callable[[Any], Coroutine[Any, Any, None]]
    predicate: Optional[Callable[[Any], bool]]  # None = always run


# Internal event envelope placed on the queue
@dataclass
class _Event:
    source_name: str
    data: Any


# ---------------------------------------------------------------------------
# CugaWatcher
# ---------------------------------------------------------------------------

class CugaWatcher:
    """
    Proactive scheduler that bridges external event sources with CugaAgent.

    Registers async data sources (polled on a schedule) and handlers
    (invoked when a source emits data matching an optional predicate).
    CugaAgent is called only from within handlers — never for empty results.

    Parameters
    ----------
    agent : CugaAgent
        The CUGA agent instance to use inside handlers.
    """

    def __init__(self, agent: CugaAgent) -> None:
        self.agent = agent
        self._sources: list[_SourceReg] = []
        self._handlers: list[_HandlerReg] = []
        self._queue: asyncio.Queue[_Event] = asyncio.Queue()
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Decorators
    # ------------------------------------------------------------------

    def source(
        self,
        every_minutes: float,
        name: str | None = None,
        run_immediately: bool = True,
    ):
        """
        Register an async coroutine as a periodic data source.

        The decorated function should return data (list, dict, str, …) or
        ``None`` / an empty collection to signal "nothing to emit".

        Parameters
        ----------
        every_minutes : float
            Polling interval in minutes.
        name : str, optional
            Logical name for this source (defaults to function name).
        run_immediately : bool
            If True (default), the source runs once on startup before the
            first sleep.

        Example
        -------
            @watcher.source(every_minutes=5)
            async def check_api():
                return await my_api.get_new_items()
        """
        def decorator(fn: Callable) -> Callable:
            reg = _SourceReg(
                name=name or fn.__name__,
                fn=fn,
                every_seconds=every_minutes * 60,
                run_immediately=run_immediately,
            )
            self._sources.append(reg)
            return fn  # return unwrapped so the fn can still be called directly
        return decorator

    def on(
        self,
        source_fn: Callable,
        when: Callable[[Any], bool] | None = None,
    ):
        """
        Register a handler coroutine to be called when a source emits data.

        Parameters
        ----------
        source_fn : Callable
            The source function (decorated with ``@watcher.source``) whose
            emissions this handler listens to.
        when : callable, optional
            A predicate ``(data) -> bool``.  The handler is only called when
            this returns ``True``.  If omitted, the handler is always called
            for every non-None emission from the source.

        Example
        -------
            @watcher.on(check_api, when=lambda items: len(items) > 0)
            async def handle_items(items):
                await watcher.agent.invoke(f"Process: {items}")
        """
        def decorator(fn: Callable) -> Callable:
            reg = _HandlerReg(
                source_name=source_fn.__name__,
                fn=fn,
                predicate=when,
            )
            self._handlers.append(reg)
            return fn
        return decorator

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start all registered sources and the event dispatcher.

        Runs forever (until ``stop()`` is called or the process is killed).
        Typically the last line in your ``main()`` coroutine.
        """
        if not self._sources:
            raise RuntimeError("CugaWatcher has no registered sources. Use @watcher.source().")

        self._running = True
        logger.info(
            f"[CugaWatcher] Starting — "
            f"{len(self._sources)} source(s), {len(self._handlers)} handler(s)"
        )

        # One async task per source + one dispatcher task
        self._tasks = [
            asyncio.create_task(self._run_source(src), name=f"source:{src.name}")
            for src in self._sources
        ]
        self._tasks.append(
            asyncio.create_task(self._dispatch(), name="dispatcher")
        )

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[CugaWatcher] Stopped.")

    async def stop(self) -> None:
        """Gracefully cancel all running tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------
    # Internal workers
    # ------------------------------------------------------------------

    async def _run_source(self, src: _SourceReg) -> None:
        """Polls a source on its schedule and puts non-empty results on the queue."""
        first_run = src.run_immediately
        while self._running:
            if not first_run:
                await asyncio.sleep(src.every_seconds)
            first_run = False

            try:
                data = await src.fn()
            except Exception:
                logger.exception(f"[CugaWatcher] Source '{src.name}' raised an exception")
                continue

            # Emit only if the source produced something meaningful
            if data is None:
                continue
            if isinstance(data, (list, dict, str, bytes)) and len(data) == 0:
                continue

            logger.debug(f"[CugaWatcher] Source '{src.name}' emitted data")
            await self._queue.put(_Event(source_name=src.name, data=data))

            if not first_run:
                # Sleep AFTER emitting so the next poll is `every_seconds` from now
                pass  # sleep is at the top of the loop

    async def _dispatch(self) -> None:
        """Reads events from the queue and fans out to matching handlers."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            matching = [h for h in self._handlers if h.source_name == event.source_name]
            if not matching:
                logger.warning(
                    f"[CugaWatcher] No handlers registered for source '{event.source_name}'"
                )
                self._queue.task_done()
                continue

            for handler in matching:
                if handler.predicate is not None and not handler.predicate(event.data):
                    logger.debug(
                        f"[CugaWatcher] Handler '{handler.fn.__name__}' predicate "
                        f"returned False — skipping"
                    )
                    continue
                try:
                    await handler.fn(event.data)
                except Exception:
                    logger.exception(
                        f"[CugaWatcher] Handler '{handler.fn.__name__}' raised an exception"
                    )

            self._queue.task_done()
