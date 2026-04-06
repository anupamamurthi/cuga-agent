"""
Smart-todo agent — LangGraph React agent version.

CUGA version used:
  CugaAgent(model, tools, plugins=[CugaSkillsPlugin])

LangGraph version:
  create_react_agent(llm, tools, state_modifier=system_prompt, checkpointer=MemorySaver())

Skills are loaded from ./skills/*.md and prepended to the system prompt —
exactly what CugaSkillsPlugin does, just made explicit.

Thread history (multi-turn memory) uses LangGraph's built-in MemorySaver
instead of CugaCheckpointer.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_EXAMPLE_DIR   = Path(__file__).parent
_LG_REACT_DIR  = _EXAMPLE_DIR.parent
_SKILLS_DIR    = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_LG_REACT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Data tools — identical to the CUGA version
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

        Args:
            todo_id: Integer id of the item to complete.
        """
        _mark_done(todo_id)
        return json.dumps({"ok": True, "id": todo_id})

    return [save_todo, list_todos, mark_done]


# ---------------------------------------------------------------------------
# Skill loader  (replaces CugaSkillsPlugin)
# ---------------------------------------------------------------------------

def _load_skills() -> str:
    parts = []
    for md_file in sorted(_SKILLS_DIR.glob("*.md")):
        parts.append(md_file.read_text())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

_graph = None
_checkpointer = None


def make_agent():
    """
    Build and return a LangGraph React agent graph.

    CUGA equivalent:
        CugaAgent(model=llm, tools=tools, plugins=[CugaSkillsPlugin(skills_dir)])

    LangGraph equivalent:
        create_react_agent(llm, tools, state_modifier=system_prompt, checkpointer=MemorySaver())

    Returns the compiled graph.  Call `invoke_agent(graph, text, thread_id)` to run it.
    """
    global _graph, _checkpointer
    if _graph is not None:
        return _graph

    from langgraph.prebuilt import create_react_agent
    from langgraph.checkpoint.memory import MemorySaver
    from _llm import create_llm

    llm = create_llm(
        provider=os.getenv("LLM_PROVIDER"),
        model=os.getenv("LLM_MODEL"),
    )
    tools = _make_data_tools()
    skills = _load_skills()
    system_prompt = f"You are a smart personal assistant.\n\n# SKILLS\n\n{skills}"

    _checkpointer = MemorySaver()
    _graph = create_react_agent(
        llm,
        tools=tools,
        state_modifier=system_prompt,
        checkpointer=_checkpointer,
    )
    log.info("LangGraph React agent ready")
    return _graph


async def invoke_agent(graph, text: str, thread_id: str = "smart-todo") -> str:
    """
    Invoke the LangGraph React agent and return the last message content.

    CUGA equivalent:
        result = await agent.invoke(text, thread_id=thread_id)
        return result.answer

    LangGraph:
        result = await graph.ainvoke({"messages": [("human", text)]}, config=config)
        return result["messages"][-1].content
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = await graph.ainvoke(
        {"messages": [("human", text)]},
        config=config,
    )
    return result["messages"][-1].content


# ---------------------------------------------------------------------------
# Reminder watcher  (replaces CugaWatcher)
# ---------------------------------------------------------------------------

async def run_reminder_watcher(graph, stop_event):
    """
    Poll SQLite every 60 s for due reminders and fire per-item emails.

    CUGA version:
        CugaWatcher with @watcher.source(every_minutes=1) + @watcher.on(when=...)
        → EmailChannel or LogChannel delivery

    LangGraph version:
        asyncio polling loop + direct EmailChannel / LogChannel call
        (cuga++ channels are still used for delivery — no change there)
    """
    import asyncio
    import re
    from store import list_due, mark_done

    log.info("Reminder watcher started")
    while not stop_event.is_set():
        try:
            due_items = list_due()
            for item in due_items:
                mark_done(item["id"])
                log.info("Reminder firing: #%d %r", item["id"], item["content"])

                config = {"configurable": {"thread_id": f"reminder-{item['id']}"}}
                result = await graph.ainvoke(
                    {"messages": [("human",
                        f'Compose a brief styled HTML reminder for: "{item["content"]}".\n'
                        f'Return only the HTML.'
                    )]},
                    config=config,
                )
                answer = result["messages"][-1].content

                html_match = re.search(r"(<html[\s\S]*?</html>)", answer, re.IGNORECASE)
                if html_match:
                    body = html_match.group(1)
                elif re.search(r"<[a-z]+[\s>]", answer, re.IGNORECASE):
                    body = answer
                else:
                    body = (
                        f"<html><body>"
                        f"<h2>⏰ Reminder: {item['content']}</h2>"
                        f"</body></html>"
                    )

                to        = item.get("delivery_email") or os.getenv("DIGEST_TO")
                smtp_user = os.getenv("SMTP_USERNAME", "")
                smtp_pass = os.getenv("SMTP_PASSWORD", "")

                # cuga++ channels for delivery — unchanged
                from cuga_channels import EmailChannel, LogChannel
                ch = (
                    EmailChannel(
                        to=to,
                        smtp_username=smtp_user,
                        smtp_password=smtp_pass,
                        subject_prefix="⏰ Reminder",
                    )
                    if to and smtp_user and smtp_pass
                    else LogChannel()
                )
                await ch.deliver(body, {"subject": f"⏰ Reminder: {item['content']}"})

        except Exception:
            log.exception("Error in reminder watcher")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass

    log.info("Reminder watcher stopped")
