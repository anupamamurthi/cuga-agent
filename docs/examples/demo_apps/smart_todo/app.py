#!/usr/bin/env python3
"""
Smart Todo — CugaAgent demo app
================================

A minimal FastAPI app + inline HTML UI backed by CugaAgent.

  python docs/examples/demo_apps/smart_todo/app.py

Then open http://localhost:8765

Env vars
--------
  LLM_PROVIDER    rits | watsonx | openai | anthropic | litellm | ollama
  LLM_MODEL       override model name
  SMTP_USER       for daily digest emails
  SMTP_PASSWORD
  DIGEST_TO       digest recipient email
  DIGEST_SCHEDULE cron expression (default: "0 8 * * 1-5", Mon-Fri 8am)

Providers and required env vars:
  rits       RITS_API_KEY
  watsonx    WATSONX_APIKEY + WATSONX_PROJECT_ID
  openai     OPENAI_API_KEY
  anthropic  ANTHROPIC_API_KEY  (pip install langchain-anthropic)
  litellm    LITELLM_API_KEY + LITELLM_BASE_URL
  ollama     OLLAMA_BASE_URL (default: http://localhost:11434)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

_EXAMPLE_DIR = Path(__file__).parent
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("smart_todo")

from store import init_db, list_all, mark_done
init_db()

app = FastAPI(title="Smart Todo", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


_watcher = None
_host    = None
_planner = None

_DIGEST_RUNTIME_ID = "smart-todo-digest"


@app.on_event("startup")
async def _startup():
    """
    Start cuga++ infrastructure:
      - CugaHost   : owns the digest CugaRuntime, persists it, restores on restart
      - CugaWatcher: polls SQLite for due reminders → agent → EmailChannel
      - ChannelPlanner: NL reconfiguration via /configure
    """
    import asyncio
    from agent import make_digest_runtime_factory, make_watcher, make_todo_planner
    from cuga_channels import CugaHost
    global _watcher, _host, _planner

    # CugaHost — digest pipeline (CronChannel → agent → EmailChannel)
    _host = CugaHost(state_dir=_EXAMPLE_DIR / ".cuga" / "host")
    _host.register_factory("digest", make_digest_runtime_factory())
    await _host.start_background()

    # Register initial digest runtime if not already persisted
    from cuga_channels import CugaHostClient
    client = CugaHostClient()
    runtimes = await client.list_runtimes()
    if not any(r["id"] == _DIGEST_RUNTIME_ID for r in runtimes):
        import os
        await client.start_runtime(_DIGEST_RUNTIME_ID, "digest", {
            "schedule": os.getenv("DIGEST_SCHEDULE", "0 8 * * 1-5"),
            "email":    os.getenv("DIGEST_TO"),
        })

    # CugaWatcher — per-item reminder firing (not channel-based: per-item logic)
    _watcher = make_watcher()
    asyncio.create_task(_watcher.start())

    # ChannelPlanner — NL reconfiguration
    _planner = make_todo_planner()

    log.info("Smart-todo started — CugaHost (digest) + CugaWatcher (reminders) active")


@app.on_event("shutdown")
async def _shutdown():
    if _host is not None:
        await _host.stop()
    if _watcher is not None:
        await _watcher.stop()


class AddRequest(BaseModel):
    text: str


@app.post("/add")
async def add_todo(req: AddRequest):
    """Submit raw text → CugaAgent classifies, saves, returns result + reasoning."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    from agent import process
    result = await process(req.text.strip())
    return result


@app.get("/todos")
def get_todos():
    return list_all(status="active")


@app.post("/done/{todo_id}")
def complete_todo(todo_id: int):
    mark_done(todo_id)
    return {"ok": True}


@app.post("/configure")
async def configure(req: AddRequest):
    """
    Reconfigure cuga++ at runtime via natural language.

    Examples:
      "send my digest at 9am instead"
      "email my digest to me@example.com daily at noon"
      "stop the digest"
      "status"
    """
    if _planner is None:
        raise HTTPException(status_code=503, detail="Planner not ready")

    from cuga_channels import CugaHostClient

    pr     = await _planner.invoke(req.text.strip())
    client = CugaHostClient()

    if pr.config:
        # ChannelPlanner extracted a new schedule/email → CugaHost hot-reconfigures
        await client.update_runtime(_DIGEST_RUNTIME_ID, "digest", pr.config)

    elif pr.action == "stop":
        try:
            await client.stop_runtime(_DIGEST_RUNTIME_ID)
        except Exception:
            pass

    elif pr.action == "status":
        try:
            info = await client.get_runtime(_DIGEST_RUNTIME_ID)
            return {"answer": pr.answer, "runtime": info}
        except Exception:
            return {"answer": "No active digest runtime.", "runtime": None}

    return {"answer": pr.answer, "action": pr.action, "config": pr.config}


@app.get("/", response_class=HTMLResponse)
def ui():
    return _HTML


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Todo · CugaAgent</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f0f13;
    color: #e2e2e8;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 40px 16px 80px;
  }
  h1 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 4px; }
  .subtitle { font-size: 13px; color: #6b6b7e; margin-bottom: 32px; }
  .subtitle span { color: #7c7cf8; font-weight: 500; }
  .capture { width: 100%; max-width: 560px; display: flex; gap: 8px; }
  input[type="text"] {
    flex: 1; background: #1a1a24; border: 1px solid #2e2e40;
    border-radius: 10px; padding: 12px 16px; font-size: 14px;
    color: #e2e2e8; outline: none; transition: border-color .15s;
  }
  input[type="text"]::placeholder { color: #4a4a60; }
  input[type="text"]:focus { border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }
  button.add-btn {
    background: #6366f1; color: #fff; border: none; border-radius: 10px;
    padding: 0 20px; font-size: 14px; font-weight: 600; cursor: pointer;
    transition: background .15s, opacity .15s; white-space: nowrap;
  }
  button.add-btn:hover { background: #4f52d9; }
  button.add-btn:disabled { opacity: .5; cursor: default; }
  .reasoning {
    width: 100%; max-width: 560px; margin-top: 12px; padding: 10px 14px;
    background: #1a1a24; border: 1px solid #2e2e40;
    border-left: 3px solid #6366f1; border-radius: 8px;
    font-size: 13px; color: #a0a0b8; line-height: 1.5; animation: fadein .25s ease;
  }
  .reasoning .type-badge {
    display: inline-block; padding: 2px 8px; border-radius: 20px;
    font-size: 11px; font-weight: 600; margin-right: 6px;
  }
  .badge-reminder { background: #fef3c7; color: #92400e; }
  .badge-todo     { background: #d1fae5; color: #065f46; }
  .badge-note     { background: #e0e7ff; color: #3730a3; }
  .error-msg {
    width: 100%; max-width: 560px; margin-top: 10px; padding: 10px 14px;
    background: #1f1215; border: 1px solid #6b2737;
    border-radius: 8px; font-size: 13px; color: #f87171;
  }
  .list-header {
    width: 100%; max-width: 560px; margin-top: 40px; margin-bottom: 12px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .list-header h2 { font-size: 13px; font-weight: 600; color: #6b6b7e; letter-spacing: .05em; text-transform: uppercase; }
  .list-header .count { font-size: 12px; color: #4a4a60; }
  .todo-list { width: 100%; max-width: 560px; display: flex; flex-direction: column; gap: 8px; }
  .todo-card {
    background: #1a1a24; border: 1px solid #2e2e40; border-radius: 10px;
    padding: 12px 14px; display: flex; align-items: flex-start; gap: 12px;
    animation: fadein .2s ease;
  }
  .todo-card:hover { border-color: #3d3d55; }
  .done-btn {
    width: 20px; height: 20px; min-width: 20px; border-radius: 50%;
    border: 2px solid #3d3d55; background: transparent; cursor: pointer;
    margin-top: 1px; transition: border-color .15s, background .15s;
  }
  .done-btn:hover { border-color: #6366f1; background: rgba(99,102,241,.15); }
  .todo-body { flex: 1; min-width: 0; }
  .todo-content { font-size: 14px; color: #d4d4e4; line-height: 1.4; word-break: break-word; }
  .todo-meta { margin-top: 5px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 500; }
  .pill-type-reminder { background: rgba(245,158,11,.12); color: #f59e0b; border: 1px solid rgba(245,158,11,.25); }
  .pill-type-todo     { background: rgba(16,185,129,.12);  color: #10b981; border: 1px solid rgba(16,185,129,.25); }
  .pill-type-note     { background: rgba(99,102,241,.12);  color: #818cf8; border: 1px solid rgba(99,102,241,.25); }
  .pill-prio-high     { background: rgba(239,68,68,.12);   color: #f87171; border: 1px solid rgba(239,68,68,.2); }
  .pill-prio-medium   { background: rgba(234,179,8,.1);    color: #ca8a04; border: 1px solid rgba(234,179,8,.2); }
  .pill-prio-low      { background: rgba(107,114,128,.1);  color: #9ca3af; border: 1px solid rgba(107,114,128,.2); }
  .pill-due  { background: rgba(239,68,68,.1);  color: #f87171; border: 1px solid rgba(239,68,68,.2); }
  .pill-tag  { background: #1e1e2e; color: #6b6b7e; border: 1px solid #2e2e40; }
  .empty { text-align: center; color: #4a4a60; font-size: 13px; padding: 32px 0; }
  @keyframes fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }
</style>
</head>
<body>
<h1>Smart Todo</h1>
<p class="subtitle">Powered by <span>CugaAgent</span> — just type naturally</p>

<div class="capture">
  <input id="input" type="text"
    placeholder="remind me to send the report at noon  ·  set up a meeting"
    autofocus />
  <button class="add-btn" id="addBtn" onclick="add()">Add</button>
</div>

<div class="capture" style="margin-top:8px;opacity:.75">
  <input id="configInput" type="text"
    placeholder="⚙ configure: send my digest at 9am  ·  email me@x.com daily  ·  stop digest"
    onkeydown="if(event.key==='Enter')configure()" />
  <button class="add-btn" id="cfgBtn" onclick="configure()" style="background:#3d3d55">Set</button>
</div>
<div id="configMsg" style="display:none;width:100%;max-width:560px;margin-top:8px;padding:8px 14px;background:#1a1a24;border:1px solid #2e2e40;border-left:3px solid #3d3d55;border-radius:8px;font-size:13px;color:#a0a0b8;"></div>

<div id="reasoning" class="reasoning" style="display:none;"></div>
<div id="error" class="error-msg" style="display:none;"></div>

<div class="list-header">
  <h2>Active</h2>
  <span class="count" id="count"></span>
</div>
<div class="todo-list" id="list"></div>

<script>
const input   = document.getElementById('input')
const addBtn  = document.getElementById('addBtn')
const rBox    = document.getElementById('reasoning')
const errBox  = document.getElementById('error')
const listEl  = document.getElementById('list')
const countEl = document.getElementById('count')

input.addEventListener('keydown', e => { if (e.key === 'Enter') add() })

async function configure() {
  const text = document.getElementById('configInput').value.trim()
  if (!text) return
  const btn = document.getElementById('cfgBtn')
  const msg = document.getElementById('configMsg')
  btn.disabled = true; btn.textContent = '…'
  try {
    const res = await fetch('/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    const data = await res.json()
    msg.textContent = data.answer || 'Done.'
    msg.style.display = 'block'
    document.getElementById('configInput').value = ''
    setTimeout(() => { msg.style.display = 'none' }, 6000)
  } catch (err) {
    msg.textContent = 'Error: ' + err.message
    msg.style.display = 'block'
  } finally {
    btn.disabled = false; btn.textContent = 'Set'
  }
}

async function add() {
  const text = input.value.trim()
  if (!text) return

  addBtn.disabled = true
  addBtn.textContent = '…'
  rBox.style.display = 'none'
  errBox.style.display = 'none'

  try {
    const res = await fetch('/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()

    input.value = ''

    const todo = data.todo || {}
    const typeIcon = { reminder:'⏰', todo:'✅', note:'💡' }[todo.todo_type] || '📝'
    const typeClass = `badge-${todo.todo_type || 'todo'}`
    rBox.innerHTML =
      `<span class="type-badge ${typeClass}">${typeIcon} ${todo.todo_type || 'saved'}</span>` +
      escHtml(data.reasoning || 'Saved.')
    rBox.style.display = 'block'
    setTimeout(() => { rBox.style.display = 'none' }, 7000)

    await loadTodos()
  } catch (err) {
    errBox.textContent = 'Error: ' + err.message
    errBox.style.display = 'block'
    setTimeout(() => { errBox.style.display = 'none' }, 5000)
  } finally {
    addBtn.disabled = false
    addBtn.textContent = 'Add'
  }
}

async function done(id) {
  await fetch('/done/' + id, { method: 'POST' })
  await loadTodos()
}

async function loadTodos() {
  const todos = await fetch('/todos').then(r => r.json())
  countEl.textContent = todos.length + ' item' + (todos.length !== 1 ? 's' : '')

  if (!todos.length) {
    listEl.innerHTML = '<div class="empty">Nothing here yet. Add something above.</div>'
    return
  }

  listEl.innerHTML = todos.map(t => {
    const typeClass = `pill-type-${t.todo_type || 'todo'}`
    const typeIcon  = { reminder:'⏰', todo:'✅', note:'💡' }[t.todo_type] || '📝'
    const prioClass = `pill-prio-${t.priority || 'medium'}`
    const duePill   = t.due_date ? `<span class="pill pill-due">⏰ ${fmtDate(t.due_date)}</span>` : ''
    const tagPills  = (t.tags || []).map(tag => `<span class="pill pill-tag">#${escHtml(tag)}</span>`).join('')

    return `
      <div class="todo-card" id="card-${t.id}">
        <button class="done-btn" onclick="done(${t.id})" title="Mark done"></button>
        <div class="todo-body">
          <div class="todo-content">${escHtml(t.content)}</div>
          <div class="todo-meta">
            <span class="pill ${typeClass}">${typeIcon} ${t.todo_type || 'todo'}</span>
            <span class="pill ${prioClass}">${t.priority || 'medium'}</span>
            ${duePill}${tagPills}
          </div>
        </div>
      </div>`
  }).join('')
}

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    })
  } catch { return iso }
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
}

loadTodos()
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import argparse
    import os
    import textwrap

    parser = argparse.ArgumentParser(
        description="Smart Todo — CugaAgent demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python app.py --provider rits
              python app.py --provider watsonx --model meta-llama/llama-4-scout-17b
              python app.py --provider openai --model gpt-4o
              python app.py --provider ollama --model llama3.1:8b
        """),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    provider_label = args.provider or os.environ.get("LLM_PROVIDER", "auto")
    print(f"\n  Smart Todo  →  http://{args.host}:{args.port}  [{provider_label}]\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
