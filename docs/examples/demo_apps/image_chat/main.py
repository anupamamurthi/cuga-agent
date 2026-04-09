"""
Image & Document Analyst — web UI with docling
===============================================

Upload images and PDFs via the browser or drop them into the inbox folder.
The agent extracts content with docling and produces structured analysis reports.

Background watcher: polls ./inbox every N seconds for new files and auto-analyzes them.
Email alerts: configurable — get notified when analysis contains specific keywords.

Run:
    python main.py
    python main.py --port 18795
    python main.py --provider anthropic

Then open: http://127.0.0.1:18795

Environment variables:
    LLM_PROVIDER      rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL         model override
    POLL_SECONDS      inbox poll interval (default: 15)
    SMTP_HOST         SMTP server
    SMTP_USERNAME     sender email
    SMTP_PASSWORD     app password
    ALERT_TO          recipient for alerts

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

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp", ".gif"}

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
# SQLite analysis log
# ---------------------------------------------------------------------------

_DB_PATH = _DIR / "analyses.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS analyses (
    id         TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    analysis   TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'upload',
    alerted    INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    with _db() as con:
        con.execute(_CREATE_SQL)


def _save_analysis(filename: str, analysis: str, source: str = "upload", alerted: bool = False) -> dict:
    aid = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _db() as con:
        con.execute(
            "INSERT INTO analyses (id, filename, analysis, source, alerted, created_at) VALUES (?,?,?,?,?,?)",
            (aid, filename, analysis, source, int(alerted), now),
        )
    return {"id": aid, "filename": filename, "analysis": analysis,
            "source": source, "alerted": alerted, "created_at": now}


def _list_analyses(limit: int = 50) -> list[dict]:
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tool: docling image/PDF extraction
# ---------------------------------------------------------------------------

def _make_tools():
    from langchain_core.tools import tool

    @tool
    def analyze_image(file_path: str) -> str:
        """
        Extract the full text and structure from an image or PDF using docling.
        Handles PNG, JPEG, TIFF, BMP, GIF (OCR) and PDF (layout-aware extraction).
        Returns clean markdown preserving tables, headings, and code blocks.

        Args:
            file_path: Absolute or relative path to the image or PDF file.
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
            return f"Error during extraction: {exc}"
        if not markdown.strip():
            return "(docling extracted no text — image may be purely graphical)"
        return markdown

    return [analyze_image]


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
        log.info("[EMAIL — not configured] %s", subject)
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
        log.info("Alert email sent → %s", cfg["to"])
        return True
    except Exception as exc:
        log.error("Email failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Background inbox watcher
# ---------------------------------------------------------------------------

_watcher_status = {"running": False, "processed": 0, "last_check": None}


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

            log.info("Analyzing: %s", fpath.name)
            try:
                result = await agent.invoke(
                    f"A new file has been dropped: {dest}\n\n"
                    f"Analyze it using the analyze_image tool and return a "
                    f"structured intelligence report.",
                    thread_id=f"analyze-{fpath.stem}",
                )
                analysis = result.answer
                analysis_lower = analysis.lower()
                alerted = False
                if keywords and any(kw in analysis_lower for kw in keywords):
                    matched = [kw for kw in keywords if kw in analysis_lower]
                    subj    = f"📷 Image Alert: {fpath.name} — keywords: {', '.join(matched)}"
                    alerted = _send_email(subj, f"File: {fpath.name}\n\nAnalysis:\n{analysis}")
                _save_analysis(fpath.name, analysis, source="watcher", alerted=alerted)
                _watcher_status["processed"] += 1
            except Exception as exc:
                log.error("Analysis error %s: %s", fpath.name, exc)
                _save_analysis(fpath.name, f"Error: {exc}", source="watcher")

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

    app = FastAPI(title="Image Analyst")
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(_inbox_watcher(agent))
        log.info("Inbox watcher started.")

    @app.post("/upload")
    async def api_upload(file: UploadFile = File(...)):
        inbox    = _DIR / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        fname    = file.filename or f"upload_{uuid.uuid4().hex[:8]}"
        dest     = inbox / fname
        content  = await file.read()
        dest.write_bytes(content)
        return {"ok": True, "filename": fname,
                "message": "File queued for analysis."}

    @app.post("/ask")
    async def api_ask(req: AskReq):
        try:
            result = await agent.invoke(req.question, thread_id="chat")
            return {"answer": result.answer}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/analyses")
    async def api_analyses():
        return _list_analyses()

    @app.get("/watcher/status")
    async def api_watcher():
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
# HTML
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Image & Doc Analyst</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:#0f1117;color:#e2e8f0;min-height:100vh}

  header{background:#1a1a2e;border-bottom:1px solid #2d2d4a;padding:14px 28px;
    display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10}
  header h1{font-size:16px;font-weight:700;color:#fff}
  .badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
  .badge-green{background:#14532d;color:#4ade80}
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

  .drop-zone{border:2px dashed #374151;border-radius:8px;padding:24px 14px;
    text-align:center;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
  .drop-zone:hover,.drop-zone.drag-over{border-color:#16a34a;background:rgba(22,163,74,.08)}
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
  input:focus{border-color:#16a34a}
  .btn{padding:5px 14px;border-radius:6px;font-size:12px;font-weight:500;
    cursor:pointer;border:none;background:#16a34a;color:#fff;transition:background .15s}
  .btn:hover{background:#15803d}
  .btn:disabled{background:#374151;color:#6b7280;cursor:default}
  .btn-sm{padding:3px 10px;font-size:11px}
  .btn-ghost{background:#1f2937;border:1px solid #374151;color:#9ca3af}
  .btn-ghost:hover{background:#374151}
  .save-ok{color:#4ade80;font-size:11px;margin-left:6px;display:none}

  .tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}
  .tag{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
    background:#1f2937;border:1px solid #374151;border-radius:10px;font-size:11px;color:#9ca3af}
  .tag-del{cursor:pointer;color:#6b7280}
  .tag-del:hover{color:#f87171}
  .kw-input-row{display:flex;gap:6px}

  .chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:11px}
  .chip{padding:4px 10px;border-radius:12px;font-size:11px;background:#1f2937;
    border:1px solid #374151;color:#9ca3af;cursor:pointer;transition:all .15s}
  .chip:hover{background:#16a34a;border-color:#16a34a;color:#fff}
  .chat-row{display:flex;gap:8px}
  .chat-input{flex:1;padding:8px 12px;border-radius:7px;font-size:13px;
    background:#0f1117;border:1px solid #374151;color:#e2e8f0;outline:none}
  .chat-input:focus{border-color:#16a34a}
  .chat-send{padding:8px 16px;border-radius:7px;font-size:13px;cursor:pointer;
    border:none;background:#16a34a;color:#fff}
  .chat-send:hover{background:#15803d}
  .chat-send:disabled{background:#374151;color:#6b7280;cursor:default}
  .chat-result{margin-top:12px;padding:12px;border-radius:7px;background:#0f1117;
    border:1px solid #2d2d4a;font-size:13px;line-height:1.6;color:#d1d5db;
    white-space:pre-wrap;display:none}
  .chat-result.vis{display:block}

  .analysis-item{border:1px solid #2d2d4a;border-radius:7px;margin-bottom:10px}
  .analysis-header{padding:10px 14px;display:flex;align-items:center;gap:8px;cursor:pointer}
  .analysis-header:hover{background:#1f2937;border-radius:7px 7px 0 0}
  .analysis-filename{font-size:12px;font-weight:600;color:#c5cae9;flex:1;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .analysis-time{font-size:10px;color:#6b7280}
  .analysis-src{font-size:10px;padding:1px 6px;border-radius:8px}
  .src-watcher{background:#1e3a5f;color:#60a5fa}
  .src-upload{background:#1c2e1c;color:#86efac}
  .alerted-badge{font-size:10px;background:#451a03;color:#fbbf24;
    padding:1px 6px;border-radius:8px}
  .analysis-body{padding:10px 14px;font-size:12px;line-height:1.6;color:#d1d5db;
    white-space:pre-wrap;border-top:1px solid #2d2d4a;background:#0f1117;display:none}
  .analysis-body.open{display:block}
  .empty-state{font-size:13px;color:#4b5563;text-align:center;padding:32px}
</style>
</head>
<body>

<header>
  <h1>🖼️ Image &amp; Doc Analyst</h1>
  <span class="badge badge-green" id="watcher-badge">Watching</span>
  <div class="spacer"></div>
  <span class="hdr-stat" id="hdr-stat">—</span>
</header>

<div class="layout">

  <!-- ── Left ─────────────────────────────────────────── -->
  <div>

    <div class="card">
      <div class="card-header"><h2>📥 Upload File</h2></div>
      <div class="card-body">
        <div class="drop-zone" id="drop-zone"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="handleDrop(event)">
          <input type="file" id="file-input" accept=".png,.jpg,.jpeg,.pdf,.tiff,.bmp,.gif"
                 onchange="uploadFile(this.files[0])">
          <div class="dz-icon">📄</div>
          <p>Drop image or PDF here, or click to upload</p>
          <small>.png · .jpg · .pdf · .tiff · .bmp</small>
        </div>
        <div id="upload-status" style="font-size:12px;margin-top:8px;display:none"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h2>⚙️ Watcher Settings</h2></div>
      <div class="card-body">
        <div class="srow">
          <label>Poll every</label>
          <input type="number" id="poll-seconds" value="15" min="5">
          <span style="font-size:11px;color:#6b7280">sec</span>
        </div>
        <button class="btn btn-sm" onclick="savePoll()">Save</button>
        <span class="save-ok" id="poll-ok">✓ Saved</span>

        <div style="font-size:11px;font-weight:600;color:#6b7280;
          text-transform:uppercase;letter-spacing:.5px;margin:12px 0 6px">
          Email alert on keywords
        </div>
        <div class="tag-row" id="kw-tags"></div>
        <div class="kw-input-row">
          <input type="text" id="kw-input" placeholder="invoice, signature, urgent…"
                 onkeydown="if(event.key==='Enter')addKeyword()">
          <button class="btn btn-sm btn-ghost" onclick="addKeyword()">+ Add</button>
        </div>
        <button class="btn btn-sm" style="margin-top:8px" onclick="saveKeywords()">Save keywords</button>
        <span class="save-ok" id="kw-ok">✓ Saved</span>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h2>✉️ Email Alerts</h2></div>
      <div class="card-body">
        <div class="srow"><label>SMTP host</label>
          <input type="text" id="smtp-host" placeholder="smtp.gmail.com"></div>
        <div class="srow"><label>Username</label>
          <input type="email" id="smtp-user" placeholder="you@gmail.com"></div>
        <div class="srow"><label>Password</label>
          <input type="password" id="smtp-pass" placeholder="app password"></div>
        <div class="srow"><label>Alert to</label>
          <input type="email" id="smtp-to" placeholder="recipient@example.com"></div>
        <button class="btn btn-sm" onclick="saveEmail()">Save</button>
        <span class="save-ok" id="email-ok">✓ Saved</span>
      </div>
    </div>

  </div><!-- /left -->

  <!-- ── Right ─────────────────────────────────────────── -->
  <div>

    <div class="card">
      <div class="card-header"><h2>💬 Ask About Documents</h2></div>
      <div class="card-body">
        <div class="chips">
          <span class="chip" onclick="ask(this.textContent)">What was in the last uploaded file?</span>
          <span class="chip" onclick="ask(this.textContent)">Were there any invoices today?</span>
          <span class="chip" onclick="ask(this.textContent)">List all files with tables</span>
          <span class="chip" onclick="ask(this.textContent)">Summarize the recent analyses</span>
          <span class="chip" onclick="ask(this.textContent)">Any files with signatures or stamps?</span>
          <span class="chip" onclick="ask(this.textContent)">What key data was extracted today?</span>
          <span class="chip" onclick="ask(this.textContent)">Were there any error extractions?</span>
          <span class="chip" onclick="ask(this.textContent)">What document types were processed?</span>
        </div>
        <div class="chat-row">
          <input class="chat-input" id="chat-input" type="text"
            placeholder="Ask about uploaded images or documents…"
            onkeydown="if(event.key==='Enter')ask()">
          <button class="chat-send" id="chat-send" onclick="ask()">Ask</button>
        </div>
        <div class="chat-result" id="chat-result"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>🗂️ Analysis Log</h2>
        <span id="analysis-count" style="font-size:11px;color:#6b7280;margin-left:auto"></span>
        <button class="btn btn-sm btn-ghost" style="margin-left:8px" onclick="loadAnalyses()">↺</button>
      </div>
      <div class="card-body" id="analyses-body">
        <div class="empty-state">No analyses yet — upload a file or drop one in ./inbox/</div>
      </div>
    </div>

  </div><!-- /right -->

</div>

<script>
let _keywords = [];

async function init() {
  await loadSettings();
  await loadAnalyses();
  updateWatcher();
  setInterval(loadAnalyses, 10000);
  setInterval(updateWatcher, 8000);
}

async function updateWatcher() {
  try {
    const s = await fetch('/watcher/status').then(r => r.json());
    document.getElementById('hdr-stat').textContent =
      `${s.processed} files analyzed · last check ${s.last_check ? new Date(s.last_check).toLocaleTimeString() : '—'}`;
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
    document.getElementById('smtp-to').value   = e.to   || '';
    _keywords = s.alert_keywords || [];
    renderKeywords();
  } catch(e) {}
}

function renderKeywords() {
  document.getElementById('kw-tags').innerHTML = _keywords.map((kw, i) =>
    `<span class="tag">${kw}<span class="tag-del" onclick="removeKw(${i})">×</span></span>`
  ).join('');
}
function addKeyword() {
  const inp = document.getElementById('kw-input');
  const v   = inp.value.trim();
  if (v && !_keywords.includes(v)) { _keywords.push(v); renderKeywords(); }
  inp.value = '';
}
function removeKw(i) { _keywords.splice(i, 1); renderKeywords(); }

async function savePoll() {
  await fetch('/settings/poll', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ poll_seconds: +document.getElementById('poll-seconds').value }) });
  flash('poll-ok');
}
async function saveKeywords() {
  await fetch('/settings/keywords', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ keywords: _keywords }) });
  flash('kw-ok');
}
async function saveEmail() {
  const pass = document.getElementById('smtp-pass').value;
  await fetch('/settings/email', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      host:     document.getElementById('smtp-host').value,
      user:     document.getElementById('smtp-user').value,
      password: pass === '••••••••' ? undefined : pass,
      to:       document.getElementById('smtp-to').value,
    }) });
  flash('email-ok');
}

async function uploadFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  const status = document.getElementById('upload-status');
  status.style.display = 'block';
  status.style.color = '#9ca3af';
  status.textContent = `Uploading ${file.name}…`;
  try {
    const r = await fetch('/upload', { method:'POST', body: fd });
    const d = await r.json();
    status.style.color = '#4ade80';
    status.textContent = `✓ ${d.message || d.filename + ' queued'}`;
    setTimeout(() => { status.style.display = 'none'; loadAnalyses(); }, 3000);
  } catch(e) {
    status.style.color = '#f87171';
    status.textContent = 'Upload error: ' + e.message;
  }
  // reset file input so same file can be re-uploaded
  document.getElementById('file-input').value = '';
}

function handleDrop(event) {
  event.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag-over');
  const f = event.dataTransfer.files[0];
  if (f) uploadFile(f);
}

async function ask(question) {
  const inp = document.getElementById('chat-input');
  const res = document.getElementById('chat-result');
  const btn = document.getElementById('chat-send');
  const q   = question || inp.value.trim();
  if (!q) return;
  inp.value = q;
  btn.disabled = true; btn.textContent = 'Thinking…';
  res.className = 'chat-result vis';
  res.textContent = 'Asking agent…';
  try {
    // Inject recent analyses as context
    const analyses = await fetch('/analyses').then(r => r.json());
    const ctx = analyses.slice(0, 5).map(a =>
      `File: ${a.filename}\nAnalysis: ${a.analysis.slice(0, 400)}`).join('\n\n');
    const prompt = ctx ? `Recent analyses:\n${ctx}\n\nQuestion: ${q}` : q;
    const r = await fetch('/ask', { method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ question: prompt }) });
    const d = await r.json();
    res.textContent = d.answer || d.error || '(no response)';
  } catch(e) { res.textContent = 'Error: ' + e.message; }
  btn.disabled = false; btn.textContent = 'Ask';
}

async function loadAnalyses() {
  try {
    const items = await fetch('/analyses').then(r => r.json());
    document.getElementById('analysis-count').textContent = items.length + ' analyses';
    renderAnalyses(items);
  } catch(e) {}
}

function renderAnalyses(items) {
  const body = document.getElementById('analyses-body');
  if (!items.length) {
    body.innerHTML = '<div class="empty-state">No analyses yet — upload a file.</div>';
    return;
  }
  body.innerHTML = items.map((item, i) => `
    <div class="analysis-item">
      <div class="analysis-header" onclick="toggleAnalysis('ab-${i}','ai-${i}')">
        <span class="analysis-filename">${esc(item.filename)}</span>
        <span class="analysis-src src-${item.source}">${item.source}</span>
        ${item.alerted ? '<span class="alerted-badge">📧 alerted</span>' : ''}
        <span class="analysis-time">${new Date(item.created_at).toLocaleString()}</span>
        <span id="ai-${i}" style="font-size:11px;color:#4b5563;margin-left:4px">▸</span>
      </div>
      <div class="analysis-body" id="ab-${i}">${esc(item.analysis)}</div>
    </div>`).join('');
}

function toggleAnalysis(bodyId, iconId) {
  document.getElementById(bodyId).classList.toggle('open');
  const icon = document.getElementById(iconId);
  icon.textContent = document.getElementById(bodyId).classList.contains('open') ? '▾' : '▸';
}

function flash(id) {
  const el = document.getElementById(id);
  el.style.display = 'inline';
  setTimeout(() => el.style.display = 'none', 2000);
}
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

init();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Image & Doc Analyst — web UI")
    parser.add_argument("--port",     type=int, default=18795)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Image & Doc Analyst  →  http://127.0.0.1:{args.port}\n")
    _web(args.port)
