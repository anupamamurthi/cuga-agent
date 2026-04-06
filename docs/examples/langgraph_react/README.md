# LangGraph React Agent Examples

Same four use cases as the CUGA demos — rebuilt with
[`langgraph.prebuilt.create_react_agent`](https://langchain-ai.github.io/langgraph/reference/prebuilt/#langgraph.prebuilt.chat_agent_executor.create_react_agent)
instead of `CugaAgent`, so you can compare the two approaches directly.

## What changed vs CUGA

| Concern | CUGA version | LangGraph version |
|---|---|---|
| **Agent loop** | `CugaAgent` | `create_react_agent` |
| **Skill injection** | `CugaSkillsPlugin` | Manual `Path("skills/*.md").read_text()` → system prompt |
| **Thread history** | `CugaCheckpointer` | `langgraph.checkpoint.memory.MemorySaver` |
| **Browser chat UI** | `ConversationGateway` | FastAPI + inline HTML/JS (hand-rolled) |
| **Pipeline planner** | `ChannelPlanner` (uses CugaAgent) | `llm.with_structured_output(PipelineConfig)` |
| **Background pipeline** | `CugaHost` + `CugaHostClient` | `asyncio` tasks with direct channel calls |

## What stayed the same (cuga++ channels)

All four examples still use the **cuga++ channel layer** for infrastructure:

- `RssChannel` — feed polling with keyword filtering
- `EmailChannel` — SMTP delivery
- `LogChannel` — stdout fallback
- `CronChannel` — scheduled triggers  
- `ChannelBuffer` — decoupling buffer

The key question this comparison answers: **what does CugaAgent add on top of LangGraph's create_react_agent?**

## Examples

| Directory | What it does |
|---|---|
| [`catalog_agent/`](catalog_agent/) | Tool injection via plugins vs manual tool list |
| [`smart_todo/`](smart_todo/) | Personal assistant with background digest pipeline |
| [`newsletter/`](newsletter/) | RSS monitor with NL pipeline config |
| [`video_qa/`](video_qa/) | Video transcription + semantic Q&A with timestamps |

## Running

Each example has its own entry point. All share `_llm.py` for multi-provider LLM support.

```bash
# catalog agent (runs a fixed set of demo tasks)
cd catalog_agent && python main.py --provider anthropic

# smart todo (browser chat at http://127.0.0.1:8765)
cd smart_todo && python app.py --provider anthropic

# newsletter (terminal REPL)
cd newsletter && python chat.py --provider anthropic

# video Q&A (CLI or web UI)
cd video_qa && python run.py meeting.mp4 --provider anthropic
cd video_qa && python run.py --web
```

## Dependencies

```
langgraph>=0.2
langchain-core
langchain-openai   # or langchain-anthropic / langchain-ibm / etc.
fastapi uvicorn    # smart_todo, video_qa web UI
apscheduler        # smart_todo background digest
faster-whisper chromadb sentence-transformers  # video_qa
feedparser         # newsletter
cuga-channels      # shared RSS / Email / Log / Cron channels
```
