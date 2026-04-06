"""
Newsletter agent — LangGraph React agent version.

CUGA version:
    CugaAgent(model, plugins=[CugaSkillsPlugin(skills_dir)])
    → invoked with a list of RSS items, returns HTML newsletter

LangGraph version:
    create_react_agent(llm, tools=[], state_modifier=system_prompt, checkpointer=MemorySaver())
    → same invocation pattern, same HTML output

Note: The newsletter agent has no tools — it reads items passed in the message
and composes HTML.  This highlights that create_react_agent works fine with
zero tools (it just uses the LLM directly, no tool loop needed).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_EXAMPLE_DIR  = Path(__file__).parent
_LG_REACT_DIR = _EXAMPLE_DIR.parent
_SKILLS_DIR   = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_LG_REACT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_skills() -> str:
    parts = []
    for md_file in sorted(_SKILLS_DIR.glob("*.md")):
        parts.append(md_file.read_text())
    return "\n\n".join(parts)


def make_agent():
    """
    Build and return a LangGraph React agent graph for newsletter curation.

    CUGA equivalent:
        CugaAgent(model=llm, plugins=[CugaSkillsPlugin(skills_dir)])

    LangGraph equivalent:
        create_react_agent(llm, tools=[], state_modifier=system_prompt, checkpointer=MemorySaver())
    """
    from langgraph.prebuilt import create_react_agent
    from langgraph.checkpoint.memory import MemorySaver
    from _llm import create_llm

    llm = create_llm(
        provider=os.getenv("LLM_PROVIDER"),
        model=os.getenv("LLM_MODEL"),
    )
    skills = _load_skills()
    system_prompt = (
        "You are an AI newsletter curator.\n\n"
        "# SKILLS\n\n"
        f"{skills}"
    )
    checkpointer = MemorySaver()
    graph = create_react_agent(
        llm,
        tools=[],
        state_modifier=system_prompt,
        checkpointer=checkpointer,
    )
    return graph


async def curate(graph, items: list[dict], thread_id: str = "newsletter") -> str:
    """
    Invoke the newsletter agent with a list of RSS items.

    Returns the HTML newsletter as a string.

    CUGA equivalent:
        result = await agent.invoke(message, thread_id=thread_id)
        return result.answer

    LangGraph:
        result = await graph.ainvoke({"messages": [("human", message)]}, config=config)
        return result["messages"][-1].content
    """
    message = (
        f"Curate and compose an HTML newsletter from the following RSS items.\n\n"
        f"Items ({len(items)}):\n"
        f"{json.dumps(items, ensure_ascii=False, indent=2)}"
    )
    config = {"configurable": {"thread_id": thread_id}}
    result = await graph.ainvoke(
        {"messages": [("human", message)]},
        config=config,
    )
    return result["messages"][-1].content
