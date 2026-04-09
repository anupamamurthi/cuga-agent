"""
Document Pipeline — inbox folder watcher + document extraction + web UI
=======================================================================

Drop PDFs or images into the inbox folder. The background watcher detects them,
extracts text with docling, has the agent classify and summarize each document,
and logs results to SQLite.

Email alerts: configure keywords — if an extracted document matches, get notified.

Run:
    python main.py
    python main.py --port 18796
    python main.py --provider anthropic

Then open: http://127.0.0.1:18796

Environment variables:
    LLM_PROVIDER      rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL         model override
    POLL_SECONDS      inbox poll interval (default: 15)
    SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD / ALERT_TO   email settings

Required:
    pip install docling
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import smtplib
import sqlite3
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

_DIR       = Path(__file__).parent
_DEMOS_DIR = _DIR.parent

for _p in [str(_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}

# ---------------------------------------------------------------------------
# Persistent store
# ---------------------------------------------------------------------------

_STORE_PATH = _DIR / ".store.json"


def _load_store() -> dict:
    try:
        if _STORE_PATH.exists():
            return json.loads(_STORE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_store(data: dict) -> None:
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def _get_email_cfg() -> dict:
    s = _load_store().get("email", {})
    return {
        "host":     s.get("host")     or os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "user":     s.get("user")     or os.getenv("SMTP_USERNAME", ""),
        "password": s.get("password") or os.getenv("SMTP_PASSWORD", ""),
        "to":       s.get("to")       or os.getenv("ALERT_TO", ""),
    }


# ---------------------------------------------------------------------------
# SQLite document log
# ---------------------------------------------------------------------------

_DB_PATH = _DIR / "pipeline.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    doc_type    TEXT NOT NULL DEFAULT 'unknown',
    report      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'watcher',
    alerted     INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    with _db() as con:
        con.execute(_CREATE_SQL)


def _save_doc(filename: str, doc_type: str, report: str,
              source: str = "watcher", alerted: bool = False) -> dict:
    did = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _db() as con:
        con.execute(
            "INSERT INTO documents (id, filename, doc_type, report, source, alerted, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (did, filename, doc_type, report, source, int(alerted), now),
        )
    return {"id": did, "filename": filename, "doc_type": doc_type,
            "report": report, "source": source, "alerted": alerted, "created_at": now}


def _list_docs(limit: int = 50) -> list[dict]:
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _make_tools():
    from langchain_core.tools import tool

    @tool
    def extract_text(file_path: str) -> str:
        """
        Extract text content from a PDF or image file using docling.
        Returns clean markdown with tables and structure preserved.

        Args:
            file_path: Absolute path to the PDF or image file.
        """
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            return "(docling not installed — run: pip install docling)"
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return f"Error: file not found: {path}"
        try:
            result   = DocumentConverter().convert(str(path))
            markdown = result.document.export_to_markdown()
        except Exception as exc:
            return f"Extraction error: {exc}"
        return markdown if markdown.strip() else "(no text extracted — may be graphical)"

    return [extract_text]


def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=_make_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
    )


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(subject: str, body: str) -> bool:
    cfg = _get_email_cfg()
    if not (cfg["to"] and cfg["user"] and cfg["password"]):
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["user"]
        msg["To"]      = cfg["to"]
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL(cfg.get("host", "smtp.gmail.com"), 465) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        return True
    except Exception as exc:
        log.error("Email failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Background watcher
# ---------------------------------------------------------------------------

_watcher_status = {"running": False, "processed": 0, "last_check": None}


def _infer_doc_type(filename: str, report: str) -> str:
    fn = filename.lower()
    for kw, dtype in [
        ("invoice", "Invoice"), ("receipt", "Receipt"), ("bill", "Invoice"),
        ("cv", "CV/Resume"), ("resume", "CV/Resume"),
        ("contract", "Contract"), ("agreement", "Contract"), ("nda", "Contract"),
        ("report", "Report"), ("paper", "Report"),
        ("meeting", "Meeting Notes"), ("minutes", "Meeting Notes"),
    ]:
        if kw in fn:
            return dtype
    return "Document"


async def _inbox_watcher(agent) -> None:
    _watcher_status["running"] = True
    inbox     = _DIR / "inbox"
    processed = inbox / "processed"
    inbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    while True:
        _watcher_status["last_check"] = datetime.now(timezone.utc).isoformat()
        cfg      = _load_store()
        interval = cfg.get("poll_seconds", 15)
        keywords = [kw.lower().strip() for kw in cfg.get("alert_keywords", []) if kw.strip()]

        files = [f for f in inbox.iterdir()
                 if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]

        for fpath in files:
            dest = processed / fpath.name
            try:
                shutil.move(str(fpath), str(dest))
            except Exception as exc:
                log.warning("Move failed %s: %s", fpath.name, exc)
                continue

            log.info("Processing: %s", fpath.name)
            try:
                result   = await agent.invoke(
                    f"A new document has arrived: {dest}\n\n"
                    f"Extract its content with extract_text and produce a structured intelligence report.",
                    thread_id=f"doc-{fpath.stem}",
                )
                report   = result.answer
                doc_type = _infer_doc_type(fpath.name, report)
                alerted  = False
                if keywords and any(kw in report.lower() for kw in keywords):
                    matched = [kw for kw in keywords if kw in report.lower()]
                    alerted = _send_email(
                        f"📄 Doc Alert: {fpath.name} — {doc_type}",
                        f"File: {fpath.name}\nType: {doc_type}\nKeywords: {', '.join(matched)}\n\n{report}"
                    )
                _save_doc(fpath.name, doc_type, report, source="watcher", alerted=alerted)
                _watcher_status["processed"] += 1
            except Exception as exc:
                log.error("Processing error %s: %s", fpath.name, exc)
                _save_doc(fpath.name, "Error", f"Error: {exc}", source="watcher")

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402


class AskReq(BaseModel):
    question: str


class EmailConfigReq(BaseModel):
    host: str = "smtp.gmail.com"
    user: str = ""
    password: str = ""
    to: str = ""


class KeywordsReq(BaseModel):
    keywords: list[str] = []


class PollReq(BaseModel):
    poll_seconds: int = 15


# ---------------------------------------------------------------------------
# Web app
# ---------------------------------------------------------------------------

def _web(port: int) -> None:
    import uvicorn

    _init_db()
    agent = make_agent()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        asyncio.create_task(_inbox_watcher(agent))
        log.info("Document pipeline watcher started.")
        yield

    app = FastAPI(title="Document Pipeline", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.post("/upload")
    async def api_upload(file: UploadFile = File(...)):
        inbox   = _DIR / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        fname   = file.filename or f"upload_{uuid.uuid4().hex[:8]}"
        dest    = inbox / fname
        content = await file.read()
        dest.write_bytes(content)
        return {"ok": True, "filename": fname, "message": "Queued for processing."}

    @app.post("/ask")
    async def api_ask(req: AskReq):
        try:
            docs   = _list_docs(5)
            ctx    = "\n\n".join(
                f"File: {d['filename']} ({d['doc_type']})\n{d['report'][:500]}"
                for d in docs
            )
            prompt = f"Recent documents:\n{ctx}\n\nQuestion: {req.question}" if ctx else req.question
            result = await agent.invoke(prompt, thread_id="chat")
            return {"answer": result.answer}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/documents")
    async def api_docs():
        return _list_docs()

    @app.get("/watcher/status")
    async def api_status():
        return _watcher_status

    @app.get("/settings")
    async def api_settings():
        return _load_store()

    @app.post("/settings/email")
    async def api_email(req: EmailConfigReq):
        data = _load_store()
        data["email"] = req.model_dump()
        _save_store(data)
        return {"ok": True}

    @app.post("/settings/keywords")
    async def api_keywords(req: KeywordsReq):
        data = _load_store()
        data["alert_keywords"] = req.keywords
        _save_store(data)
        return {"ok": True}

    @app.post("/settings/poll")
    async def api_poll(req: PollReq):
        data = _load_store()
        data["poll_seconds"] = req.poll_seconds
        _save_store(data)
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    async def ui():
        return HTMLResponse(_HTML)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# HTML — embedded UI
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Document Pipeline</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:#0f1117;color:#e2e8f0;min-height:100vh}
  header{background:#1a1a2e;border-bottom:1px solid #2d2d4a;padding:14px 28px;
    display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10}
  header h1{font-size:16px;font-weight:700;color:#fff}
  .badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;
    background:#451a03;color:#fbbf24}
  .spacer{flex:1}
  .hdr-stat{font-size:11px;color:#4b5563}
  .layout{display:grid;grid-template-columns:320px 1fr;gap:20px;
    max-width:1280px;margin:0 auto;padding:20px 24px}
  .card{background:#1a1a2e;border:1px solid #2d2d4a;border-radius:10px;
    overflow:hidden;margin-bottom:16px}
  .card-header{padding:12px 16px 10px;border-bottom:1px solid #2d2d4a;
    display:flex;align-items:center;gap:8px}
  .card-header h2{font-size:13px;font-weight:600;color:#c5cae9}
  .card-body{padding:16px}
  .drop-zone{border:2px dashed #374151;border-radius:8px;padding:22px 14px;
    text-align:center;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
  .drop-zone:hover,.drop-zone.drag-over{border-color:#d97706;background:rgba(217,119,6,.08)}
  .drop-zone input[type=file]{position:absolute;inset:0;width:100%;height:100%;
    opacity:0;cursor:pointer;z-index:2}
  .dz-icon{font-size:28px;margin-bottom:6px}
  .drop-zone p{font-size:12px;color:#9ca3af}
  .drop-zone small{font-size:11px;color:#4b5563}
  .srow{display:flex;align-items:center;gap:8px;margin-bottom:9px}
  .srow label{font-size:12px;color:#9ca3af;min-width:90px}
  input[type=text],input[type=password],input[type=email],input[type=number]{flex:1;
    padding:5px 9px;border-radius:5px;font-size:12px;background:#0f1117;
    border:1px solid #374151;color:#e2e8f0;outline:none}
  input:focus{border-color:#d97706}
  .btn{padding:5px 14px;border-radius:6px;font-size:12px;font-weight:500;
    cursor:pointer;border:none;background:#d97706;color:#fff}
  .btn:hover{background:#b45309}
  .btn:disabled{background:#374151;color:#6b7280;cursor:default}
  .btn-sm{padding:3px 10px;font-size:11px}
  .btn-ghost{background:#1f2937;border:1px solid #374151;color:#9ca3af}
  .btn-ghost:hover{background:#374151}
  .save-ok{color:#4ade80;font-size:11px;margin-left:6px;display:none}
  .tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}
  .tag{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
    background:#1f2937;border:1px solid #374151;border-radius:10px;font-size:11px;color:#9ca3af}
  .tag-del{cursor:pointer;color:#6b7280}.tag-del:hover{color:#f87171}
  .kw-row{display:flex;gap:6px}
  .chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:11px}
  .chip{padding:4px 10px;border-radius:12px;font-size:11px;background:#1f2937;
    border:1px solid #374151;color:#9ca3af;cursor:pointer;transition:all .15s}
  .chip:hover{background:#d97706;border-color:#d97706;color:#fff}
  .chat-row{display:flex;gap:8px}
  .chat-input{flex:1;padding:8px 12px;border-radius:7px;font-size:13px;
    background:#0f1117;border:1px solid #374151;color:#e2e8f0;outline:none}
  .chat-input:focus{border-color:#d97706}
  .chat-send{padding:8px 16px;border-radius:7px;font-size:13px;cursor:pointer;
    border:none;background:#d97706;color:#fff}
  .chat-send:hover{background:#b45309}
  .chat-send:disabled{background:#374151;color:#6b7280;cursor:default}
  .chat-result{margin-top:12px;padding:12px;border-radius:7px;background:#0f1117;
    border:1px solid #2d2d4a;font-size:13px;line-height:1.6;color:#d1d5db;
    white-space:pre-wrap;display:none}
  .chat-result.vis{display:block}
  .doc-item{border:1px solid #2d2d4a;border-radius:7px;margin-bottom:10px}
  .doc-header{padding:10px 14px;display:flex;align-items:center;gap:8px;cursor:pointer}
  .doc-header:hover{background:#1f2937;border-radius:7px 7px 0 0}
  .doc-filename{font-size:12px;font-weight:600;color:#c5cae9;flex:1;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .doc-type{font-size:10px;padding:1px 6px;border-radius:8px;background:#451a03;color:#fbbf24}
  .doc-time{font-size:10px;color:#6b7280}
  .alert-badge{font-size:10px;background:#052e16;color:#4ade80;padding:1px 6px;border-radius:8px}
  .doc-body{padding:10px 14px;font-size:12px;line-height:1.6;color:#d1d5db;
    white-space:pre-wrap;border-top:1px solid #2d2d4a;background:#0f1117;display:none}
  .doc-body.open{display:block}
  .empty-state{font-size:13px;color:#4b5563;text-align:center;padding:32px}
</style>
</head>
<body>
<header>
  <h1>🗂️ Document Pipeline</h1>
  <span class="badge" id="watcher-badge">Watching</span>
  <div class="spacer"></div>
  <span class="hdr-stat" id="hdr-stat">—</span>
</header>
<div class="layout">
  <div>
    <div class="card">
      <div class="card-header"><h2>📥 Upload Document</h2></div>
      <div class="card-body">
        <div class="drop-zone" id="drop-zone"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="handleDrop(event)">
          <input type="file" id="file-input" accept=".pdf,.png,.jpg,.jpeg,.tiff,.bmp"
                 onchange="uploadFile(this.files[0])">
          <div class="dz-icon">📂</div>
          <p>Drop PDF or image here, or click to upload</p>
          <small>.pdf · .png · .jpg · .tiff</small>
        </div>
        <div id="upload-status" style="font-size:12px;margin-top:8px;display:none"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h2>⚙️ Settings</h2></div>
      <div class="card-body">
        <div class="srow">
          <label>Poll every</label>
          <input type="number" id="poll-seconds" value="15" min="5">
          <span style="font-size:11px;color:#6b7280">sec</span>
        </div>
        <button class="btn btn-sm" onclick="savePoll()">Save</button>
        <span class="save-ok" id="poll-ok">✓</span>
        <div style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;
          letter-spacing:.5px;margin:12px 0 6px">Alert keywords</div>
        <div class="tag-row" id="kw-tags"></div>
        <div class="kw-row">
          <input type="text" id="kw-input" placeholder="invoice, contract, urgent…"
                 onkeydown="if(event.key==='Enter')addKeyword()">
          <button class="btn btn-sm btn-ghost" onclick="addKeyword()">+ Add</button>
        </div>
        <button class="btn btn-sm" style="margin-top:8px" onclick="saveKeywords()">Save keywords</button>
        <span class="save-ok" id="kw-ok">✓</span>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h2>✉️ Email Alerts</h2></div>
      <div class="card-body">
        <div class="srow"><label>SMTP host</label><input type="text" id="smtp-host" placeholder="smtp.gmail.com"></div>
        <div class="srow"><label>Username</label><input type="email" id="smtp-user" placeholder="you@gmail.com"></div>
        <div class="srow"><label>Password</label><input type="password" id="smtp-pass" placeholder="app password"></div>
        <div class="srow"><label>Alert to</label><input type="email" id="smtp-to" placeholder="recipient@example.com"></div>
        <button class="btn btn-sm" onclick="saveEmail()">Save</button>
        <span class="save-ok" id="email-ok">✓</span>
      </div>
    </div>
  </div>
  <div>
    <div class="card">
      <div class="card-header"><h2>💬 Ask About Documents</h2></div>
      <div class="card-body">
        <div class="chips">
          <span class="chip" onclick="ask(this.textContent)">What invoices were processed today?</span>
          <span class="chip" onclick="ask(this.textContent)">List all contracts in the pipeline</span>
          <span class="chip" onclick="ask(this.textContent)">What was the total on the last invoice?</span>
          <span class="chip" onclick="ask(this.textContent)">Any documents with action items?</span>
          <span class="chip" onclick="ask(this.textContent)">Summarize all documents this week</span>
          <span class="chip" onclick="ask(this.textContent)">Which CVs were received?</span>
          <span class="chip" onclick="ask(this.textContent)">Are there expiring contracts?</span>
          <span class="chip" onclick="ask(this.textContent)">What document types were processed?</span>
        </div>
        <div class="chat-row">
          <input class="chat-input" id="chat-input" type="text"
            placeholder="Ask about processed documents…"
            onkeydown="if(event.key==='Enter')ask()">
          <button class="chat-send" id="chat-send" onclick="ask()">Ask</button>
        </div>
        <div class="chat-result" id="chat-result"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <h2>📋 Document Log</h2>
        <span id="doc-count" style="font-size:11px;color:#6b7280;margin-left:auto"></span>
        <button class="btn btn-sm btn-ghost" style="margin-left:8px" onclick="loadDocs()">↺</button>
      </div>
      <div class="card-body" id="docs-body">
        <div class="empty-state">No documents yet — upload one or drop into ./inbox/</div>
      </div>
    </div>
  </div>
</div>
<script>
let _keywords = [];
async function init() {
  await loadSettings(); await loadDocs(); updateWatcher();
  setInterval(loadDocs, 10000); setInterval(updateWatcher, 8000);
}
async function updateWatcher() {
  try {
    const s = await fetch('/watcher/status').then(r => r.json());
    document.getElementById('hdr-stat').textContent =
      s.processed + ' documents processed · last check ' + (s.last_check ? new Date(s.last_check).toLocaleTimeString() : '—');
  } catch(e) {}
}
async function loadSettings() {
  try {
    const s = await fetch('/settings').then(r => r.json());
    document.getElementById('poll-seconds').value = s.poll_seconds || 15;
    const e = s.email || {};
    document.getElementById('smtp-host').value = e.host || '';
    document.getElementById('smtp-user').value = e.user || '';
    document.getElementById('smtp-pass').value = e.password ? '••••••••' : '';
    document.getElementById('smtp-to').value   = e.to || '';
    _keywords = s.alert_keywords || []; renderKeywords();
  } catch(e) {}
}
function renderKeywords() {
  document.getElementById('kw-tags').innerHTML = _keywords.map((kw,i) =>
    `<span class="tag">${kw}<span class="tag-del" onclick="removeKw(${i})">×</span></span>`).join('');
}
function addKeyword() {
  const inp=document.getElementById('kw-input'), v=inp.value.trim();
  if(v && !_keywords.includes(v)){_keywords.push(v);renderKeywords();} inp.value='';
}
function removeKw(i){_keywords.splice(i,1);renderKeywords();}
async function savePoll() {
  await fetch('/settings/poll',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({poll_seconds:+document.getElementById('poll-seconds').value})});
  flash('poll-ok');
}
async function saveKeywords() {
  await fetch('/settings/keywords',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({keywords:_keywords})});
  flash('kw-ok');
}
async function saveEmail() {
  const pass=document.getElementById('smtp-pass').value;
  await fetch('/settings/email',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({host:document.getElementById('smtp-host').value,
      user:document.getElementById('smtp-user').value,
      password:pass==='••••••••'?undefined:pass,
      to:document.getElementById('smtp-to').value})});
  flash('email-ok');
}
async function uploadFile(file) {
  if(!file) return;
  const fd=new FormData(); fd.append('file',file);
  const status=document.getElementById('upload-status');
  status.style.display='block'; status.textContent='Uploading '+file.name+'…';
  try {
    const r=await fetch('/upload',{method:'POST',body:fd}), d=await r.json();
    status.textContent='✓ '+d.message;
    setTimeout(()=>{status.style.display='none';loadDocs();},3000);
  } catch(e){status.textContent='Error: '+e.message;}
}
function handleDrop(event) {
  event.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag-over');
  const f=event.dataTransfer.files[0]; if(f) uploadFile(f);
}
async function ask(question) {
  const inp=document.getElementById('chat-input'),res=document.getElementById('chat-result'),
    btn=document.getElementById('chat-send'),q=question||inp.value.trim();
  if(!q) return;
  inp.value=q; btn.disabled=true; btn.textContent='Thinking…';
  res.className='chat-result vis'; res.textContent='Thinking…';
  try {
    const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q})}), d=await r.json();
    res.textContent=d.answer||d.error||'(no response)';
  } catch(e){res.textContent='Error: '+e.message;}
  btn.disabled=false; btn.textContent='Ask';
}
async function loadDocs() {
  try {
    const docs=await fetch('/documents').then(r=>r.json());
    document.getElementById('doc-count').textContent=docs.length+' documents';
    const body=document.getElementById('docs-body');
    if(!docs.length){body.innerHTML='<div class="empty-state">No documents yet.</div>';return;}
    body.innerHTML=docs.map((d,i)=>`
      <div class="doc-item">
        <div class="doc-header" onclick="toggleDoc('db-${i}','di-${i}')">
          <span class="doc-filename">${esc(d.filename)}</span>
          <span class="doc-type">${esc(d.doc_type)}</span>
          ${d.alerted?'<span class="alert-badge">📧 alerted</span>':''}
          <span class="doc-time">${new Date(d.created_at).toLocaleString()}</span>
          <span id="di-${i}" style="font-size:11px;color:#4b5563;margin-left:4px">▸</span>
        </div>
        <div class="doc-body" id="db-${i}">${esc(d.report)}</div>
      </div>`).join('');
  } catch(e){}
}
function toggleDoc(bodyId,iconId) {
  document.getElementById(bodyId).classList.toggle('open');
  const icon=document.getElementById(iconId);
  icon.textContent=document.getElementById(bodyId).classList.contains('open')?'▾':'▸';
}
function flash(id){const el=document.getElementById(id);el.style.display='inline';setTimeout(()=>el.style.display='none',2000);}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
init();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Document Pipeline — web UI")
    parser.add_argument("--port",     type=int, default=18796)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Document Pipeline  →  http://127.0.0.1:{args.port}\n")
    _web(args.port)
