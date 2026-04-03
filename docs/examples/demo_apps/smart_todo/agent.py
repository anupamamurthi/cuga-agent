"""
Smart-todo agent — built on CugaAgent + cuga++ channels.

Components
----------
  CugaAgent          — LangGraph ReAct loop; handles user requests
  CugaRuntime        — digest: CronChannel wakes agent → EmailChannel delivers
  CugaWatcher        — reminders: polls SQLite for due items → fires per-item;
                       EmailChannel delivers result (agent never calls send)

Tools on CugaAgent
------------------
  save_todo          — persist a classified todo/reminder/note
  list_todos         — read active or done items (used by digest trigger)

Delivery is owned by cuga++ (EmailChannel / LogChannel).
The agent never calls a send_email tool.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
_SKILLS_DIR  = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# LangChain tools  (data access only — no delivery tools)
# ---------------------------------------------------------------------------

def _make_tools():
    from langchain_core.tools import tool
    from store import save, list_all

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

    return [save_todo, list_todos]


# ---------------------------------------------------------------------------
# Singleton CugaAgent
# ---------------------------------------------------------------------------

_agent = None


def get_agent():
    """Build (or return the cached) CugaAgent with data-access tools only."""
    global _agent
    if _agent is not None:
        return _agent

    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    llm = create_llm(
        provider=os.getenv("LLM_PROVIDER"),
        model=os.getenv("LLM_MODEL"),
    )

    _agent = CugaAgent(
        model=llm,
        tools=_make_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )
    log.info("Smart-todo CugaAgent ready")
    return _agent


# ---------------------------------------------------------------------------
# Digest RuntimeFactory  — registered with CugaHost
#
# Returns a factory function (config dict → CugaRuntime).
# CugaHost calls this when starting or restoring the digest runtime.
# The config dict is fully serializable so CugaHost can persist and restore it.
# ---------------------------------------------------------------------------

_DIGEST_MESSAGE = (
    "Good morning! Compile and send the daily todo digest.\n"
    "1. Call list_todos(status='active') to get open items.\n"
    "2. Call list_todos(status='done') to get items completed since yesterday.\n"
    "Compose a single styled HTML email with two sections: "
    "'✅ Completed' (done items) and '📋 Still open' (active items, grouped by priority).\n"
    "Return the HTML — do not call any send or email tools."
)


def make_digest_runtime_factory():
    """
    Return a RuntimeFactory for the todo digest pipeline.

    Register with CugaHost:
        host.register_factory("digest", make_digest_runtime_factory())

    Config dict keys:
        schedule  str   cron expression (default "0 8 * * 1-5")
        email     str   recipient address (default from DIGEST_TO env var)
    """
    from cuga_channels import CugaRuntime, CronChannel, EmailChannel, LogChannel

    def factory(config: dict):
        schedule   = config.get("schedule") or os.getenv("DIGEST_SCHEDULE", "0 8 * * 1-5")
        email      = config.get("email")    or os.getenv("DIGEST_TO")
        smtp_ready = bool(os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD") and email)

        output_channels = (
            [
                EmailChannel(
                    to=email,
                    smtp_username=os.getenv("SMTP_USERNAME", ""),
                    smtp_password=os.getenv("SMTP_PASSWORD", ""),
                    subject_prefix="📋 Smart Todo Digest",
                ),
                LogChannel(),
            ]
            if smtp_ready
            else [LogChannel()]
        )

        return CugaRuntime(
            agent=get_agent(),
            input_channels=[
                CronChannel(schedule=schedule, message=_DIGEST_MESSAGE, name="digest-cron"),
            ],
            output_channels=output_channels,
            thread_id="smart-todo-digest",
            require_buffer=False,
        )

    return factory


# ---------------------------------------------------------------------------
# CugaWatcher — reminder pub-sub
# ---------------------------------------------------------------------------

def make_watcher():
    """
    Build a CugaWatcher that watches SQLite for due reminders.

    Source  : check_due_reminders  — polls list_due() every 60 s
                                     emits only when rows exist ([] is dropped)
    Handler : fire_reminders        — marks each item done, invokes agent once
                                     per reminder; EmailChannel delivers the result
    """
    from cuga_channels import EmailChannel, LogChannel
    from cuga_watcher import CugaWatcher
    from store import list_due, mark_done

    smtp_ready = bool(os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))
    _email_ch = (
        EmailChannel.from_env(subject_prefix="⏰ Reminder", to_env_var="DIGEST_TO")
        if smtp_ready
        else LogChannel()
    )

    agent   = get_agent()
    watcher = CugaWatcher(agent=agent)

    @watcher.source(every_minutes=1, name="check_due_reminders")
    async def check_due_reminders():
        return list_due()   # [] → CugaWatcher silently drops, agent never called

    @watcher.on(check_due_reminders, when=lambda items: len(items) > 0)
    async def fire_reminders(items):
        for item in items:
            mark_done(item["id"])   # mark done first — prevents double-fire
            log.info("Reminder firing: #%d %r", item["id"], item["content"])
            result = await watcher.agent.invoke(
                f'Compose a brief styled HTML reminder email body for: "{item["content"]}".\n'
                f'Return only the HTML — do not call any send or email tools.',
                thread_id=f"reminder-{item['id']}",
            )
            await _email_ch.deliver(
                result.answer,
                {"subject": f"⏰ Reminder: {item['content']}"},
            )

    return watcher


# ---------------------------------------------------------------------------
# ChannelPlanner  — runtime reconfiguration via natural language
#
# Allows the user to say things like:
#   "send my digest at 9am instead"
#   "stop the digest"
#   "email my digest to me@example.com daily at noon"
#
# The same ChannelPlanner pattern used by newsletter/chat.py — generic in
# cuga-channels, app-specific host prompt and tools here.
# ---------------------------------------------------------------------------

_TODO_HOST_PROMPT = """\
You are a configuration assistant for the Smart Todo app powered by cuga++.

Available cuga++ channels
--------------------------
TriggerChannels:
  CronChannel  — fires the daily digest on a cron schedule

OutputChannels:
  EmailChannel — sends the HTML digest via SMTP
  LogChannel   — prints to stdout (when no email is configured)

Your job
--------
Interpret the user's configuration request and call one of:

  configure_digest(schedule, email)
    Reconfigure when the digest fires and where it is delivered.
    schedule: cron expression extracted from natural language (see below).
    email:    recipient address if mentioned; null to keep current / use stdout.

  stop_digest()
    Stop the running digest runtime.

  get_digest_status()
    Report current digest configuration.

Extracting cron schedule from natural language
----------------------------------------------
  "every morning at 8"     →  "0 8 * * *"
  "weekdays at 9am"        →  "0 9 * * 1-5"
  "every day at noon"      →  "0 12 * * *"
  "every hour"             →  "0 * * * *"
  "every 30 minutes"       →  "*/30 * * * *"
  "Monday mornings at 7"   →  "0 7 * * 1"
  "twice a day"            →  "0 8,20 * * *"

If no schedule is mentioned, keep the current default: "0 8 * * 1-5".
"""


def _make_todo_planning_tools(state: dict) -> list:
    from langchain_core.tools import tool

    @tool
    def configure_digest(
        schedule: str = "0 8 * * 1-5",
        email: str | None = None,
    ) -> str:
        """
        Reconfigure the todo digest schedule and delivery.

        Args:
            schedule: Cron expression for when to send the digest.
            email:    Recipient email address; null to use stdout / current env.
        """
        state["config"] = {"schedule": schedule, "email": email}
        dest = f"→ {email}" if email else "→ current delivery channel"
        return f"Digest reconfigured: '{schedule}', {dest}."

    @tool
    def stop_digest() -> str:
        """Stop the currently running digest runtime."""
        state["action"] = "stop"
        return "Stop requested."

    @tool
    def get_digest_status() -> str:
        """Report whether the digest is running and its current config."""
        state["action"] = "status"
        return "Status requested."

    return [configure_digest, stop_digest, get_digest_status]


def make_todo_planner(provider: str | None = None, model: str | None = None):
    """
    Build a ChannelPlanner for Smart Todo runtime reconfiguration.

    Used by app.py's /configure endpoint — lets the user reconfigure the
    digest schedule and delivery channel via natural language at runtime.
    """
    from cuga import CugaAgent
    from cuga_channels import ChannelPlanner
    from _llm import create_llm

    state = {}
    agent = CugaAgent(
        model=create_llm(
            provider=provider or os.getenv("LLM_PROVIDER"),
            model=model or os.getenv("LLM_MODEL"),
        ),
        tools=_make_todo_planning_tools(state),
        cuga_folder=str(_EXAMPLE_DIR / ".cuga" / "planning"),
    )
    return ChannelPlanner(
        agent=agent,
        host_prompt=_TODO_HOST_PROMPT,
        state=state,
        thread_id="todo-planner",
    )


# ---------------------------------------------------------------------------
# Public API  (used by app.py /add endpoint)
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
