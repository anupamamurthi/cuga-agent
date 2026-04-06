"""
Smart-todo agent tools.

OpenClaw model: one agent, all capabilities as tools.
  Data tools    — save_todo, list_todos, mark_done   (CRUD on the store)
  Config tools  — configure_digest, stop_digest, get_digest_status
                  (wire into CugaHost via CugaHostClient — only included
                   when a client is provided)

Delivery (EmailChannel) is owned by cuga++, never by the agent.
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
# Data tools — the only interface between the agent and app data
# ---------------------------------------------------------------------------

def _make_data_tools():
    from langchain_core.tools import tool
    from store import save, list_all, mark_done as _mark_done

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
        Save a classified todo, reminder, or note.

        Args:
            content:        Clean task description.
            todo_type:      "todo" | "reminder" | "note"
            priority:       "high" | "medium" | "low"
            tags:           List of tag strings.
            due_date:       ISO-8601 string for reminders; null otherwise.
            delivery_email: Email to send this reminder to when it fires.
                            Extract from user input if mentioned; null for default.
        """
        item = save(
            content=content, todo_type=todo_type, priority=priority,
            tags=tags, due_date=due_date, delivery_email=delivery_email,
        )
        return json.dumps(item)

    @tool
    def list_todos(status: str = "active") -> str:
        """
        Return all todos as a JSON array.

        Args:
            status: "active" (default) or "done"
        """
        return json.dumps(list_all(status=status))

    @tool
    def mark_done(todo_id: int) -> str:
        """
        Mark a todo item as completed.
        Call list_todos first to find the correct id if you don't know it.

        Args:
            todo_id: Integer id of the item to complete.
        """
        _mark_done(todo_id)
        return json.dumps({"ok": True, "id": todo_id})

    return [save_todo, list_todos, mark_done]


# ---------------------------------------------------------------------------
# Config tools — let the agent reconfigure the background digest pipeline
# (OpenClaw model: infrastructure config is just another set of tools)
# ---------------------------------------------------------------------------

def _make_config_tools(client, runtime_id: str = "smart-todo-digest"):
    """
    Generate async LangChain tools that reconfigure the digest CugaRuntime.

    The agent calls these exactly like data tools — no separate planner agent,
    no ChannelPlanner, no separate config UI. This is the OpenClaw model.
    """
    from langchain_core.tools import tool

    @tool
    async def configure_digest(
        schedule: str = "0 8 * * 1-5",
        email: str | None = None,
    ) -> str:
        """
        Reconfigure the daily todo digest.

        Args:
            schedule: Cron expression, e.g. "0 9 * * *" for 9am daily.
                      Extract from natural language:
                        "every morning at 8"  → "0 8 * * *"
                        "weekdays at 9am"     → "0 9 * * 1-5"
                        "every 30 minutes"    → "*/30 * * * *"
            email:    Recipient address; null to keep current.
        """
        await client.update_runtime(runtime_id, "digest", {
            "schedule": schedule,
            "email":    email,
        })
        dest = email or "stdout (no email configured)"
        return f"Digest reconfigured — schedule: {schedule}, delivery: {dest}"

    @tool
    async def stop_digest() -> str:
        """Stop the running daily digest."""
        try:
            await client.stop_runtime(runtime_id)
            return "Digest stopped."
        except Exception:
            return "No active digest to stop."

    @tool
    async def get_digest_status() -> str:
        """Report the current digest schedule and delivery configuration."""
        try:
            info = await client.get_runtime(runtime_id)
            cfg  = info.get("config", {})
            return (
                f"Digest is running — schedule: {cfg.get('schedule', '?')}, "
                f"delivery: {cfg.get('email') or 'stdout'}"
            )
        except Exception:
            return "No digest runtime is currently running."

    return [configure_digest, stop_digest, get_digest_status]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def make_agent(client=None):
    """
    Build a CugaAgent with data tools + optional config tools.

    Parameters
    ----------
    client  CugaHostClient instance.  When provided, config tools
            (configure_digest, stop_digest, get_digest_status) are added
            so the user can reconfigure the pipeline through conversation.
            Pass None for a read/write-only agent (e.g. in host_factories).
    """
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    tools = _make_data_tools()
    if client is not None:
        tools += _make_config_tools(client)

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=tools,
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )


# ---------------------------------------------------------------------------
# CugaWatcher — fires per-item reminders when due_date is reached
# ---------------------------------------------------------------------------

def make_watcher(agent):
    """
    Build a CugaWatcher that polls SQLite for due reminders and fires them.

    Source:  polls list_due() every 60 s; empty list is silently dropped.
    Handler: marks each item done, invokes agent to compose the email body,
             delivers via per-item email (if set) or default channel.
    """
    from cuga_channels import smart_deliver
    from cuga_watcher import CugaWatcher
    from store import list_due, mark_done

    watcher = CugaWatcher(agent=agent)

    @watcher.source(every_minutes=1, name="check_due_reminders")
    async def check_due_reminders():
        return list_due()

    @watcher.on(check_due_reminders, when=lambda items: len(items) > 0)
    async def fire_reminders(items):
        import re
        for item in items:
            mark_done(item["id"])
            log.info("Reminder firing: #%d %r", item["id"], item["content"])
            result = await watcher.agent.invoke(
                f'Compose a brief styled HTML reminder for: "{item["content"]}".\n'
                f'Return only the HTML.',
                thread_id=f"reminder-{item['id']}",
            )

            # Extract HTML from answer — agent sometimes returns prose + HTML,
            # sometimes just prose.  Fall back to a simple template if no HTML found.
            answer = result.answer
            html_match = re.search(r"(<html[\s\S]*?</html>)", answer, re.IGNORECASE)
            if html_match:
                body = html_match.group(1)
            elif re.search(r"<[a-z]+[\s>]", answer, re.IGNORECASE):
                body = answer  # partial HTML — use as-is
            else:
                body = (
                    f"<html><body>"
                    f"<h2>⏰ Reminder: {item['content']}</h2>"
                    f"</body></html>"
                )

            # Deliver via email when SMTP is configured, log otherwise.
            # Per-item delivery_email takes priority over the default DIGEST_TO.
            await smart_deliver(
                body,
                subject_prefix="⏰ Reminder",
                metadata={"subject": f"⏰ Reminder: {item['content']}"},
                to=item.get("delivery_email"),  # falls back to DIGEST_TO env var
            )

    return watcher
