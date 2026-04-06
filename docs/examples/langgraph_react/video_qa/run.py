#!/usr/bin/env python3
"""
Video Q&A — LangGraph React agent version.

Same modes as the CUGA version:
  1. Interactive CLI Q&A:   python run.py meeting.mp4
  2. Single question:       python run.py meeting.mp4 --ask "where was M3 discussed?"
  3. Web UI:                python run.py --web
                            python run.py meeting.mp4 --web

The only internal change: VideoQAAgent now wraps create_react_agent instead
of CugaAgent.  The public API (agent.transcribe / agent.ask) is unchanged,
so this run.py is nearly identical to the CUGA version.

Dependencies (install once):
    pip install faster-whisper chromadb sentence-transformers fastapi uvicorn
    brew install ffmpeg

Env vars:
    LLM_PROVIDER    rits | watsonx | openai | anthropic | litellm | ollama
    LLM_MODEL       model name override
    RITS_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / etc.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import textwrap
from pathlib import Path

_EXAMPLE_DIR  = Path(__file__).parent
_LG_REACT_DIR = _EXAMPLE_DIR.parent

for _p in [str(_EXAMPLE_DIR), str(_LG_REACT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("video_qa")


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

async def _cli(
    video_path: str | None,
    question: str | None,
    whisper_model: str,
    provider: str | None,
    model: str | None,
):
    from agent import VideoQAAgent

    agent = VideoQAAgent(provider=provider, model=model, whisper_model=whisper_model)

    if video_path:
        path = Path(video_path).expanduser().resolve()
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

        print(f"\nVideo: {path.name}")
        print("Transcribing… (cached on disk after first run)")
        info = await agent.transcribe(str(path))
        print(f"Done — {info['segments_count']} segments, duration {info['duration_fmt']}\n")
    else:
        print("\nNo video loaded. Use `transcribe_video` tool by asking to load a file.\n")

    if question:
        print(f"Q: {question}")
        answer = await agent.ask(question)
        print(f"A: {answer}\n")
        return

    # Interactive loop
    print("Ask questions about the video. Type 'exit' to quit.\n")
    while True:
        try:
            q = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if q.lower() in {"exit", "quit", "bye"}:
            print("Bye.")
            break
        if not q:
            continue
        answer = await agent.ask(q)
        print(f"Agent: {answer}\n")


# ---------------------------------------------------------------------------
# Web UI mode
# ---------------------------------------------------------------------------

_WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Video Q&A — LangGraph React</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f13; color: #e0e0e8; font-family: -apple-system, sans-serif; height: 100vh; display: flex; flex-direction: column; }
  header { background: #1a1a2e; padding: 14px 20px; border-bottom: 1px solid #2a2a3e; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 16px; font-weight: 600; }
  header span { font-size: 11px; color: #7070a0; background: #252540; padding: 2px 8px; border-radius: 20px; }
  #status-bar { font-size: 12px; color: #7070a0; padding: 8px 20px; border-bottom: 1px solid #1e1e2e; background: #12121c; }
  .layout { display: flex; flex: 1; overflow: hidden; }
  .chat { display: flex; flex-direction: column; flex: 1; }
  .messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 80%; padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
  .msg.user { align-self: flex-end; background: #2563eb; color: #fff; border-radius: 12px 12px 2px 12px; }
  .msg.agent { align-self: flex-start; background: #1e1e30; border: 1px solid #2a2a3e; border-radius: 2px 12px 12px 12px; }
  .msg.agent.loading { color: #7070a0; }
  .input-row { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #2a2a3e; background: #1a1a2e; }
  #input { flex: 1; background: #252540; border: 1px solid #3a3a5e; border-radius: 8px; padding: 10px 14px; color: #e0e0e8; font-size: 14px; outline: none; }
  #input:focus { border-color: #4466cc; }
  button { background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; cursor: pointer; }
  button:hover { background: #1d4ed8; }
  button:disabled { background: #333355; cursor: default; }
  .sidebar { width: 300px; background: #12121c; border-left: 1px solid #2a2a3e; display: flex; flex-direction: column; }
  .sidebar-header { padding: 12px; border-bottom: 1px solid #1e1e2e; }
  .sidebar-header h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; color: #7070a0; letter-spacing: 1px; margin-bottom: 8px; }
  .load-row { display: flex; gap: 6px; }
  #file-input { flex: 1; background: #1e1e30; border: 1px solid #2a2a3e; border-radius: 6px; padding: 6px 10px; color: #e0e0e8; font-size: 12px; outline: none; }
  .segments { flex: 1; overflow-y: auto; padding: 8px; }
  .seg { font-size: 11px; padding: 6px 8px; border-radius: 6px; margin-bottom: 4px; color: #9090b0; line-height: 1.4; }
  .seg:hover { background: #1e1e30; }
  .seg .ts { color: #4466cc; font-weight: 600; margin-right: 6px; }
  #search-input { width: 100%; background: #1e1e30; border: 1px solid #2a2a3e; border-radius: 6px; padding: 6px 10px; color: #e0e0e8; font-size: 12px; outline: none; margin-top: 6px; }
</style>
</head>
<body>
<header>
  <h1>Video Q&A</h1>
  <span>LangGraph React agent</span>
</header>
<div id="status-bar">No video loaded — enter a path and click Load</div>
<div class="layout">
  <div class="chat">
    <div class="messages" id="messages">
      <div class="msg agent">Hi! Load a video on the right, then ask me questions about it. I'll answer with precise timestamps.</div>
    </div>
    <div class="input-row">
      <input id="input" type="text" placeholder="Ask a question about the video…" autofocus>
      <button id="send-btn" onclick="send()">Send</button>
    </div>
  </div>
  <div class="sidebar">
    <div class="sidebar-header">
      <h2>Video</h2>
      <div class="load-row">
        <input id="file-input" type="text" placeholder="/path/to/video.mp4">
        <button onclick="loadVideo()" id="load-btn" style="padding:6px 12px;font-size:12px;">Load</button>
      </div>
      <input id="search-input" type="text" placeholder="Filter transcript…" oninput="filterSegments()">
    </div>
    <div class="segments" id="segments"></div>
  </div>
</div>
<script>
const msgs    = document.getElementById('messages');
const input   = document.getElementById('input');
const segsDiv = document.getElementById('segments');
const statusBar = document.getElementById('status-bar');
let threadId = 'vqa-' + Math.random().toString(36).slice(2, 8);
let allSegments = [];

function appendMsg(text, role) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
  return d;
}

async function loadVideo() {
  const path = document.getElementById('file-input').value.trim();
  if (!path) return;
  const btn = document.getElementById('load-btn');
  btn.disabled = true;
  btn.textContent = '…';
  statusBar.textContent = 'Transcribing (may take a minute)…';
  try {
    const r = await fetch('/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({video_path: path})
    });
    const d = await r.json();
    if (d.error) { statusBar.textContent = 'Error: ' + d.error; return; }
    statusBar.textContent = `${d.video_path.split('/').pop()} — ${d.segments_count} segments, ${d.duration_fmt}`;
    appendMsg(`Loaded ${d.video_path.split('/').pop()} — ${d.segments_count} segments, duration ${d.duration_fmt}. Ask me anything!`, 'agent');
    const sr = await fetch('/segments');
    allSegments = await sr.json();
    renderSegments(allSegments);
  } catch(e) {
    statusBar.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Load';
  }
}

function renderSegments(segs) {
  segsDiv.innerHTML = '';
  segs.forEach(s => {
    const d = document.createElement('div');
    d.className = 'seg';
    d.innerHTML = `<span class="ts">${s.start_fmt}</span>${s.text}`;
    segsDiv.appendChild(d);
  });
}

function filterSegments() {
  const q = document.getElementById('search-input').value.toLowerCase();
  renderSegments(q ? allSegments.filter(s => s.text.toLowerCase().includes(q)) : allSegments);
}

async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  appendMsg(text, 'user');
  const loading = appendMsg('Thinking…', 'agent loading');
  document.getElementById('send-btn').disabled = true;
  try {
    const r = await fetch('/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: text, thread_id: threadId})
    });
    const d = await r.json();
    loading.textContent = d.answer || d.error || 'No response';
    loading.classList.remove('loading');
  } catch(e) {
    loading.textContent = 'Error: ' + e.message;
  } finally {
    document.getElementById('send-btn').disabled = false;
  }
}

input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
</script>
</body>
</html>
"""


def _build_web_app(agent):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel

    app = FastAPI(title="Video Q&A — LangGraph React")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    class LoadRequest(BaseModel):
        video_path: str

    class AskRequest(BaseModel):
        question: str
        thread_id: str = "video-qa"

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return _WEB_HTML

    @app.post("/load")
    async def load(req: LoadRequest):
        try:
            info = await agent.transcribe(req.video_path)
            return info
        except Exception as e:
            return {"error": str(e)}

    @app.get("/segments")
    async def segments():
        return agent.segments

    @app.get("/status")
    async def status():
        return {
            "loaded": agent.video_path is not None,
            "video_path": agent.video_path,
            "segment_count": len(agent.segments),
        }

    @app.post("/ask")
    async def ask(req: AskRequest):
        if not agent.video_path:
            return {"answer": "No video loaded yet. Please load a video first."}
        try:
            answer = await agent.ask(req.question, thread_id=req.thread_id)
            return {"answer": answer}
        except Exception as e:
            return {"error": str(e)}

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Video Q&A — LangGraph React agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python run.py meeting.mp4
              python run.py meeting.mp4 --ask "where was M3 discussed?"
              python run.py --web
              python run.py meeting.mp4 --web
        """),
    )
    parser.add_argument("video",        nargs="?", default=None, help="Path to video file")
    parser.add_argument("--ask",        default=None, help="Single question (non-interactive)")
    parser.add_argument("--web",        action="store_true", help="Launch web UI")
    parser.add_argument("--host",       default="127.0.0.1")
    parser.add_argument("--port",       type=int, default=8766)
    parser.add_argument("--whisper",    default="base",
        choices=["tiny", "base", "small", "medium", "large-v3"])
    parser.add_argument("--provider",   "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",      "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    if args.web:
        import uvicorn
        from agent import VideoQAAgent

        agent = VideoQAAgent(
            provider=args.provider,
            model=args.model,
            whisper_model=args.whisper,
        )

        async def _startup():
            if args.video:
                path = Path(args.video).expanduser().resolve()
                if path.exists():
                    print(f"Pre-loading: {path.name}…")
                    info = await agent.transcribe(str(path))
                    print(f"Done — {info['segments_count']} segments, {info['duration_fmt']}")

        app = _build_web_app(agent)

        @app.on_event("startup")
        async def startup():
            await _startup()

        print(f"\n  Video Q&A (LangGraph React)  →  http://{args.host}:{args.port}\n")
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        asyncio.run(_cli(
            video_path=args.video,
            question=args.ask,
            whisper_model=args.whisper,
            provider=args.provider,
            model=args.model,
        ))


if __name__ == "__main__":
    main()
