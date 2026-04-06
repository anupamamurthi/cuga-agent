"""
Smart Todo — LangGraph React agent version.

CUGA version wired up:
  ConversationGateway(agent) → browser chat UI + WebSocket routing (free)
  CugaHost + CugaHostClient  → background digest pipeline (free)
  CugaWatcher                → reminder polling (free)

LangGraph version requires you to build all of this yourself:
  FastAPI + inline HTML/JS   → browser chat UI (hand-rolled ~200 lines)
  asyncio tasks              → digest cron + reminder watcher
  cuga++ EmailChannel        → still used for SMTP delivery (unchanged)

Run:
    python app.py
    python app.py --provider anthropic
    python app.py --provider openai --model gpt-4o
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_EXAMPLE_DIR  = Path(__file__).parent
_LG_REACT_DIR = _EXAMPLE_DIR.parent

if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))
if str(_LG_REACT_DIR) not in sys.path:
    sys.path.insert(0, str(_LG_REACT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from store import init_db
init_db()

# ---------------------------------------------------------------------------
# Inline browser UI
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Smart Todo — LangGraph React</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f13; color: #e0e0e8; font-family: -apple-system, sans-serif; height: 100vh; display: flex; flex-direction: column; }
  header { background: #1a1a2e; padding: 14px 20px; border-bottom: 1px solid #2a2a3e; display: flex; align-items: center; gap: 10px; }
  header h1 { font-size: 16px; font-weight: 600; }
  header span { font-size: 11px; color: #7070a0; background: #252540; padding: 2px 8px; border-radius: 20px; }
  .layout { display: flex; flex: 1; overflow: hidden; }
  .chat { display: flex; flex-direction: column; flex: 1; padding: 0; }
  .messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 80%; padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.5; }
  .msg.user { align-self: flex-end; background: #2563eb; color: #fff; border-radius: 12px 12px 2px 12px; }
  .msg.agent { align-self: flex-start; background: #1e1e30; border: 1px solid #2a2a3e; border-radius: 2px 12px 12px 12px; }
  .msg.agent.loading { color: #7070a0; }
  .input-row { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #2a2a3e; background: #1a1a2e; }
  #input { flex: 1; background: #252540; border: 1px solid #3a3a5e; border-radius: 8px; padding: 10px 14px; color: #e0e0e8; font-size: 14px; outline: none; }
  #input:focus { border-color: #4466cc; }
  button { background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; cursor: pointer; }
  button:hover { background: #1d4ed8; }
  .sidebar { width: 280px; background: #12121c; border-left: 1px solid #2a2a3e; overflow-y: auto; padding: 12px; }
  .sidebar h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; color: #7070a0; letter-spacing: 1px; margin-bottom: 10px; }
  .todo-card { background: #1e1e30; border: 1px solid #2a2a3e; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; font-size: 13px; }
  .todo-card .content { font-weight: 500; margin-bottom: 4px; }
  .badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 10px; margin-right: 4px; }
  .badge.todo     { background: #1e3a5f; color: #60a5fa; }
  .badge.reminder { background: #3d1f00; color: #fb923c; }
  .badge.note     { background: #1a2d1a; color: #4ade80; }
  .badge.high     { background: #3b0000; color: #f87171; }
  .badge.medium   { background: #2d2200; color: #fbbf24; }
  .badge.low      { background: #1a2d1a; color: #4ade80; }
  .done-btn { font-size: 10px; color: #7070a0; cursor: pointer; background: none; border: none; padding: 0; margin-top: 4px; }
  .done-btn:hover { color: #4ade80; }
  .refresh-btn { font-size: 11px; color: #7070a0; cursor: pointer; background: none; border: none; padding: 4px 0; margin-bottom: 8px; }
</style>
</head>
<body>
<header>
  <h1>Smart Todo</h1>
  <span>LangGraph React agent</span>
</header>
<div class="layout">
  <div class="chat">
    <div class="messages" id="messages">
      <div class="msg agent">Hi! I'm your smart todo assistant. Tell me what to add, ask to see your todos, or say something like "remind me to call John at 3pm".</div>
    </div>
    <div class="input-row">
      <input id="input" type="text" placeholder="Add a todo, reminder, or note…" autofocus>
      <button onclick="send()">Send</button>
    </div>
  </div>
  <div class="sidebar">
    <h2>Active Todos</h2>
    <button class="refresh-btn" onclick="loadTodos()">↺ Refresh</button>
    <div id="todo-list"></div>
  </div>
</div>
<script>
const msgs = document.getElementById('messages');
const input = document.getElementById('input');
const todoList = document.getElementById('todo-list');
let threadId = 'smart-todo-' + Math.random().toString(36).slice(2, 8);

function appendMsg(text, role) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
  return d;
}

async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  appendMsg(text, 'user');
  const loading = appendMsg('…', 'agent loading');
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, thread_id: threadId})
    });
    const data = await r.json();
    loading.textContent = data.response;
    loading.classList.remove('loading');
  } catch(e) {
    loading.textContent = 'Error: ' + e.message;
  }
  loadTodos();
}

async function markDone(id) {
  await fetch('/done/' + id, {method: 'POST'});
  loadTodos();
}

async function loadTodos() {
  const r = await fetch('/todos');
  const items = await r.json();
  todoList.innerHTML = '';
  if (!items.length) {
    todoList.innerHTML = '<div style="color:#505070;font-size:12px;">Nothing here yet.</div>';
    return;
  }
  items.forEach(item => {
    const d = document.createElement('div');
    d.className = 'todo-card';
    const due = item.due_date ? `<span style="font-size:11px;color:#7070a0;">⏰ ${item.due_date.replace('T',' ')}</span>` : '';
    d.innerHTML = `
      <div class="content">${item.content}</div>
      <span class="badge ${item.todo_type}">${item.todo_type}</span>
      <span class="badge ${item.priority}">${item.priority}</span>
      ${due}
      <br><button class="done-btn" onclick="markDone(${item.id})">✓ mark done</button>`;
    todoList.appendChild(d);
  });
}

input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
loadTodos();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Smart Todo — LangGraph React")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_graph = None
_stop_event = asyncio.Event()


class ChatRequest(BaseModel):
    message: str
    thread_id: str = "smart-todo"


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.post("/chat")
async def chat(req: ChatRequest):
    from agent import invoke_agent
    response = await invoke_agent(_graph, req.message, thread_id=req.thread_id)
    return {"response": response}


@app.get("/todos")
async def get_todos():
    from store import list_all
    return list_all(status="active")


@app.post("/done/{todo_id}")
async def done(todo_id: int):
    from store import mark_done
    mark_done(todo_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Background digest task  (replaces CugaHost + CronChannel)
# ---------------------------------------------------------------------------

async def _digest_loop(schedule_seconds: int):
    """
    Fire the daily digest on a fixed interval.

    CUGA version: CugaHost manages a CronChannel that fires the agent.
    LangGraph:    asyncio sleep loop that directly invokes the graph.

    What you give up: persistent schedule config (CugaHost survives restarts),
    natural-language schedule changes ("set digest to 9am"), per-runtime config.
    """
    log = logging.getLogger("smart_todo.digest")
    log.info("Digest loop started — interval %ds", schedule_seconds)
    await asyncio.sleep(schedule_seconds)  # wait before first fire
    while not _stop_event.is_set():
        log.info("Digest firing")
        try:
            from agent import invoke_agent
            html = await invoke_agent(
                _graph,
                "It's time for the daily digest. "
                "List all active and done todos, organize by priority, "
                "and return a styled HTML email.",
                thread_id="digest",
            )
            to        = os.getenv("DIGEST_TO")
            smtp_user = os.getenv("SMTP_USERNAME", "")
            smtp_pass = os.getenv("SMTP_PASSWORD", "")
            from cuga_channels import EmailChannel, LogChannel
            ch = (
                EmailChannel(
                    to=to,
                    smtp_username=smtp_user,
                    smtp_password=smtp_pass,
                    subject_prefix="📋 Daily Digest",
                )
                if to and smtp_user and smtp_pass
                else LogChannel()
            )
            await ch.deliver(html, {"subject": "📋 Smart Todo Daily Digest"})
        except Exception:
            log.exception("Digest error")

        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=schedule_seconds)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    global _graph
    from agent import make_agent, run_reminder_watcher

    _graph = make_agent()

    # Background reminder watcher
    asyncio.create_task(run_reminder_watcher(_graph, _stop_event))

    # Background digest — default: 8h interval (≈ weekday morning)
    interval_s = int(os.getenv("DIGEST_INTERVAL_SECONDS", str(8 * 3600)))
    asyncio.create_task(_digest_loop(interval_s))


@app.on_event("shutdown")
async def shutdown():
    _stop_event.set()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import textwrap
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Smart Todo — LangGraph React agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py
              python app.py --provider anthropic
              python app.py --provider openai --model gpt-4o
        """),
    )
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=8765)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Smart Todo (LangGraph React)  →  http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port)
