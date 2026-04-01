"""
Smart-todo agent — built on CugaAgent.

Tools:
  save_todo          — classify and persist a todo to SQLite
  list_todos         — read active todos
  send_digest_email  — send the daily digest via SMTP

The agent is a singleton. On first use it creates a CugaAgent with:
  - cuga-skills: injects todo_reasoning.md into the system prompt
  - CronTrigger:  daily digest fires on DIGEST_SCHEDULE (default Mon-Fri 8am)
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger(__name__)

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
_SKILLS_DIR  = _EXAMPLE_DIR / "skills"

# Make store.py and _llm.py importable
for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

def _make_tools():
    from langchain_core.tools import tool
    from store import save, list_all, mark_done

    @tool
    def save_todo(
        content: str,
        todo_type: str = "todo",
        priority: str = "medium",
        tags: list[str] | None = None,
        due_date: str | None = None,
    ) -> str:
        """
        Save a classified todo, reminder, or note to the database.

        Args:
            content:   Clean task description.
            todo_type: "todo" | "reminder" | "note"
            priority:  "high" | "medium" | "low"
            tags:      List of tag strings.
            due_date:  ISO-8601 string for reminders, null otherwise.

        Returns:
            JSON with the saved item's id and fields.
        """
        item = save(content=content, todo_type=todo_type,
                    priority=priority, tags=tags, due_date=due_date)
        return json.dumps(item)

    @tool
    def list_todos(status: str = "active") -> str:
        """
        Return all todos as a JSON array.

        Args:
            status: "active" (default) or "done"

        Returns:
            JSON array of todo objects.
        """
        items = list_all(status=status)
        return json.dumps(items)

    @tool
    def send_digest_email(subject: str, html_body: str) -> str:
        """
        Send the daily todo digest via SMTP.

        Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, DIGEST_TO from env.
        Returns "sent" or an error string.
        """
        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER", "")
        pwd  = os.getenv("SMTP_PASSWORD", "")
        to   = os.getenv("DIGEST_TO", user)

        if not user or not pwd or not to:
            return "Error: set SMTP_USER, SMTP_PASSWORD, DIGEST_TO in .env"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo(); s.starttls(context=ctx); s.login(user, pwd)
                s.sendmail(user, [to], msg.as_string())
            return f"sent to {to}"
        except Exception as exc:
            return f"Error: {exc}"

    @tool
    def list_due_reminders() -> str:
        """
        Return all active reminders whose due_date is at or before now.

        Returns:
            JSON array of reminder objects (may be empty).
        """
        from store import list_due
        items = list_due()
        return json.dumps(items)

    @tool
    def mark_todo_done(todo_id: int) -> str:
        """
        Mark a todo or reminder as done so it is not re-fired.

        Args:
            todo_id: The integer id of the item to mark done.

        Returns:
            "done" on success.
        """
        mark_done(todo_id)
        return "done"

    return [save_todo, list_todos, send_digest_email, list_due_reminders, mark_todo_done]


# ---------------------------------------------------------------------------
# ReminderTrigger — custom CugaTrigger (satisfies cuga-triggers protocol)
# ---------------------------------------------------------------------------

class ReminderTrigger:
    """
    Polls SQLite every `interval` seconds for due reminders.

    Unlike a generic CronTrigger, this trigger:
      1. Queries the DB directly in Python — no LLM involved in deciding what's due.
      2. Marks each reminder done BEFORE invoking the agent, so a crash or timeout
         cannot cause a double-fire.
      3. Invokes the agent once per due reminder with the full context in the message
         so the agent only needs to call send_digest_email and reply — no extra lookups.

    This satisfies the CugaTrigger protocol (duck-typing — no base class needed).
    """

    name = "reminder-poller"

    def __init__(self, interval: int = 60) -> None:
        from typing import Any, Callable, Coroutine, Optional
        self._interval  = interval
        self._scheduler = None
        self._invoke_fn: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None

    def start(self, invoke_fn) -> None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError as exc:
            raise ImportError(
                "apscheduler is required for ReminderTrigger. "
                "Install with: pip install 'cuga-triggers[cron]'"
            ) from exc

        self._invoke_fn = invoke_fn
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._check,
            "interval",
            seconds=self._interval,
            id=self.name,
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("ReminderTrigger started — polling every %ds", self._interval)

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("ReminderTrigger stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self) -> None:
        """Called by APScheduler in a background thread every `interval` seconds."""
        from store import list_due, mark_done
        from cuga_triggers.types import TriggerEvent
        import asyncio

        due = list_due()
        if not due:
            return

        for item in due:
            # Mark done first — prevents double-fire even if agent invocation fails
            mark_done(item["id"])
            log.info("ReminderTrigger: firing reminder #%d: %r", item["id"], item["content"])

            event = TriggerEvent(
                source=self.name,
                message=(
                    f'Send a reminder email for this item: "{item["content"]}". '
                    f'Call send_digest_email with:\n'
                    f'  subject = "⏰ Reminder: {item["content"]}"\n'
                    f'  html_body = a brief styled HTML reminder notice.\n'
                    f'Reply "sent" when done.'
                ),
                thread_id=f"reminder-{item['id']}",
            )

            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(self._invoke_fn(event), loop=loop)
            except RuntimeError:
                asyncio.run(self._invoke_fn(event))


# ---------------------------------------------------------------------------
# Singleton CugaAgent
# ---------------------------------------------------------------------------

_agent = None


def get_agent():
    """
    Build (or return the cached) CugaAgent.

    Registers a CronTrigger for the daily digest so the same agent instance
    handles both HTTP requests and scheduled digest runs.
    """
    global _agent
    if _agent is not None:
        return _agent

    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from cuga_triggers import CronTrigger

    from _llm import create_llm
    llm = create_llm(
        provider=os.getenv("LLM_PROVIDER"),
        model=os.getenv("LLM_MODEL"),
    )

    schedule = os.getenv("DIGEST_SCHEDULE", "0 8 * * 1-5")

    _agent = CugaAgent(
        model=llm,
        tools=_make_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
        triggers=[
            CronTrigger(
                name="daily-digest",
                schedule=schedule,
                message=(
                    "Good morning! Compile and send the daily todo digest. "
                    "Call list_todos, organize by priority, compose HTML, "
                    "send with send_digest_email. "
                    "Subject: '📋 Daily Todo Digest — {weekday}'"
                ),
                thread_id="smart-todo-digest",
            ),
            ReminderTrigger(interval=60),
        ],
    )
    log.info("Smart-todo CugaAgent ready — digest scheduled: %s", schedule)
    return _agent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def process(text: str, thread_id: str = "smart-todo") -> dict:
    """
    Run raw user text through CugaAgent.
    Returns {"todo": <saved item dict>, "reasoning": <agent answer>}.
    """
    agent = get_agent()

    prompt = (
        f'New todo input: "{text}"\n\n'
        "Classify it, extract fields, save it with save_todo, "
        "then reply in one sentence confirming what you did."
    )

    result = await agent.invoke(
        prompt,
        thread_id=thread_id,
        track_tool_calls=True,
    )

    # Extract the saved item from tracked tool calls
    todo_item: dict = {}
    for call in result.tool_calls:
        if call.get("name") == "save_todo":
            try:
                raw = call.get("result", "")
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and "id" in data:
                    todo_item = data
                    break
            except (json.JSONDecodeError, TypeError):
                pass

    return {"todo": todo_item, "reasoning": result.answer}
