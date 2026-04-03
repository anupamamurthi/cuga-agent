"""
Smart-todo agent — CugaAgent + cuga++ channels.

Tools exposed to CugaAgent (data access only):
  save_todo   — classify and persist a todo/reminder/note
  list_todos  — read active or done items

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
# Tools — the only interface between CUGA and app data
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
        delivery_email: str | None = None,
    ) -> str:
        """
        Save a classified todo, reminder, or note to the database.

        Args:
            content:        Clean task description.
            todo_type:      "todo" | "reminder" | "note"
            priority:       "high" | "medium" | "low"
            tags:           List of tag strings.
            due_date:       ISO-8601 string for reminders, null otherwise.
            delivery_email: Email address to send this reminder to when it fires.
                            Extract from user input if mentioned; null to use default.

        Returns:
            JSON with the saved item's id and fields.
        """
        item = save(
            content=content,
            todo_type=todo_type,
            priority=priority,
            tags=tags,
            due_date=due_date,
            delivery_email=delivery_email,
        )
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
        return json.dumps(list_all(status=status))

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
# CugaWatcher — reminder pub-sub
# ---------------------------------------------------------------------------

def make_watcher():
    """
    Build a CugaWatcher that watches SQLite for due reminders.

    Source:  polls list_due() every 60 s; empty list is silently dropped.
    Handler: marks each item done, invokes agent once per reminder,
             delivers via per-item email (if set) or default channel.
    """
    from cuga_channels import EmailChannel, LogChannel
    from cuga_watcher import CugaWatcher
    from store import list_due, mark_done

    smtp_ready = bool(os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))
    _default_ch = (
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
            mark_done(item["id"])
            log.info("Reminder firing: #%d %r", item["id"], item["content"])
            result = await watcher.agent.invoke(
                f'Compose a brief styled HTML reminder email body for: "{item["content"]}".\n'
                f'Return only the HTML — do not call any send or email tools.',
                thread_id=f"reminder-{item['id']}",
            )
            # Use per-item delivery_email if the user specified one, else default channel.
            item_email = item.get("delivery_email")
            if item_email and smtp_ready:
                ch = EmailChannel(
                    to=item_email,
                    smtp_username=os.getenv("SMTP_USERNAME", ""),
                    smtp_password=os.getenv("SMTP_PASSWORD", ""),
                    subject_prefix="⏰ Reminder",
                )
            else:
                ch = _default_ch
            await ch.deliver(result.answer, {"subject": f"⏰ Reminder: {item['content']}"})

    return watcher


# ---------------------------------------------------------------------------
# ChannelPlanner — NL reconfiguration of the digest runtime
# ---------------------------------------------------------------------------

def make_todo_planner(provider: str | None = None, model: str | None = None):
    """Build a ChannelPlanner for Smart Todo runtime reconfiguration."""
    from cuga_channels import ChannelPlanner, PlannerTool
    from _llm import create_llm

    return ChannelPlanner.from_schema(
        llm=create_llm(
            provider=provider or os.getenv("LLM_PROVIDER"),
            model=model or os.getenv("LLM_MODEL"),
        ),
        tools=[
            PlannerTool(
                name="configure_digest",
                description=(
                    "Reconfigure when the daily digest fires and where it is delivered. "
                    "Call this when the user mentions a new schedule or email address."
                ),
                params={
                    "schedule": (str, "0 8 * * 1-5", "cron expression extracted from natural language"),
                    "email":    (str, None,           "recipient email address; null to keep current"),
                },
            ),
            PlannerTool("stop_digest",       "Stop the running digest runtime."),
            PlannerTool("get_digest_status", "Report whether the digest is running and its current config."),
        ],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga" / "planning"),
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
        "Classify it, extract fields (including delivery_email if an email address is mentioned), "
        "save it with save_todo, then reply in one sentence confirming what you did."
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
                raw  = call.get("result", "")
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and "id" in data:
                    todo_item = data
                    break
            except (json.JSONDecodeError, TypeError):
                pass

    return {"todo": todo_item, "reasoning": result.answer}
