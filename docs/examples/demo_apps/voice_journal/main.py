"""
Voice Journal — personal journal with audio support + web UI
=============================================================

Record thoughts by text, file upload, or audio. The agent structures
entries and saves them to a local SQLite DB + dated Markdown files.
A background watcher polls an inbox folder for new audio/text files
and auto-saves them as journal entries.

Optional: weekly email digest of recent entries.

Run:
    python main.py
    python main.py --port 18799
    python main.py --provider anthropic

Then open: http://127.0.0.1:18799

Environment variables:
    LLM_PROVIDER      rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL         model override
    SMTP_HOST         SMTP server (default: smtp.gmail.com)
    SMTP_USERNAME     sender email
    SMTP_PASSWORD     app password
    JOURNAL_TO        recipient for weekly digest
    OPENAI_API_KEY    for Whisper transcription (optional)
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import smtplib
import sys
from datetime import datetime, timezone, date, timedelta
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

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac"}
TEXT_EXTENSIONS  = {".txt", ".md"}
ALL_EXTENSIONS   = AUDIO_EXTENSIONS | TEXT_EXTENSIONS

# ---------------------------------------------------------------------------
# Persistent settings store
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
        "to":       s.get("to")       or os.getenv("JOURNAL_TO", ""),
    }


# ---------------------------------------------------------------------------
# Audio transcription
# ---------------------------------------------------------------------------

def _transcribe(audio_path: Path) -> str:
    """Transcribe audio using OpenAI Whisper API or local whisper."""
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            with open(audio_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1", file=f
                )
            return result.text
        except Exception as exc:
            log.warning("OpenAI Whisper failed: %s — trying local whisper", exc)

    try:
        import whisper
        model  = whisper.load_model("base")
        result = model.transcribe(str(audio_path))
        return result["text"]
    except ImportError:
        return f"(Transcription unavailable — install openai or openai-whisper)\nAudio file: {audio_path.name}"
    except Exception as exc:
        return f"(Transcription error: {exc})"


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(subject: str, body_html: str) -> bool:
    cfg = _get_email_cfg()
    if not (cfg["to"] and cfg["user"] and cfg["password"]):
        log.info("[EMAIL — not configured] %s", subject)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["user"]
        msg["To"]      = cfg["to"]
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP_SSL(cfg.get("host", "smtp.gmail.com"), 465) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        log.info("Journal digest sent → %s", cfg["to"])
        return True
    except Exception as exc:
        log.error("Email failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

def _make_tools():
    import json as _json
    from langchain_core.tools import tool
    from store import save_entry, list_entries as _list_entries, list_dates as _list_dates

    @tool
    def save_journal_entry(
        body: str,
        title: str = "",
        tags: str = "",
        source: str = "text",
        entry_date: str | None = None,
    ) -> str:
        """
        Save a journal entry to local Markdown + SQLite.

        Args:
            body:       The full journal entry text (clean, structured prose).
            title:      Short title (3-7 words).
            tags:       Comma-separated keywords (mood, topic, people, place).
            source:     "text" | "voice" | "upload"
            entry_date: ISO date (YYYY-MM-DD). Defaults to today.
        """
        entry = save_entry(body=body, title=title, tags=tags, source=source, entry_date=entry_date)
        return _json.dumps(entry)

    @tool
    def list_entries(
        entry_date: str | None = None,
        since_date: str | None = None,
        until_date: str | None = None,
        limit: int = 10,
    ) -> str:
        """
        Return journal entries as JSON.

        Args:
            entry_date: Filter to specific date (YYYY-MM-DD).
            since_date: Entries on or after this date.
            until_date: Entries on or before this date.
            limit:      Max entries to return.
        """
        return _json.dumps(_list_entries(
            entry_date=entry_date,
            since_date=since_date,
            until_date=until_date,
            limit=limit,
        ))

    @tool
    def list_dates() -> str:
        """Return all dates that have journal entries, most recent first."""
        return _json.dumps(_list_dates())

    return [save_journal_entry, list_entries, list_dates]


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
# Background watcher — inbox folder for new audio/text files
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
        interval = cfg.get("poll_seconds", 20)

        files = [f for f in inbox.iterdir()
                 if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS]

        for fpath in files:
            dest = processed / fpath.name
            try:
                shutil.move(str(fpath), str(dest))
            except Exception as exc:
                log.warning("Move failed %s: %s", fpath.name, exc)
                continue

            log.info("Processing inbox file: %s", fpath.name)
            suffix = fpath.suffix.lower()

            if suffix in AUDIO_EXTENSIONS:
                transcript = _transcribe(dest)
                prompt = (
                    f"[Transcript of audio file: {fpath.name}]\n\n{transcript}\n\n"
                    f"Structure this as a journal entry, clean it up, and save it."
                )
                source = "voice"
            else:
                content = dest.read_text(encoding="utf-8", errors="replace")
                prompt = (
                    f"[File upload: {fpath.name}]\n\n{content[:6000]}\n\n"
                    f"Format this as a journal entry and save it."
                )
                source = "upload"

            try:
                result = await agent.invoke(prompt, thread_id=f"inbox-{fpath.stem}")
                log.info("Saved entry from: %s", fpath.name)
                _watcher_status["processed"] += 1
            except Exception as exc:
                log.error("Error processing %s: %s", fpath.name, exc)

        # Weekly digest check (every Sunday if configured)
        cfg = _load_store()
        if cfg.get("weekly_digest_enabled") and cfg.get("email", {}).get("to"):
            last = cfg.get("last_digest")
            now  = datetime.now(timezone.utc)
            if not last or (now - datetime.fromisoformat(last)).days >= 7:
                await _send_weekly_digest(agent, cfg)

        await asyncio.sleep(interval)


async def _send_weekly_digest(agent, cfg: dict) -> None:
    from store import list_entries
    since = (date.today() - timedelta(days=7)).isoformat()
    entries = list_entries(since_date=since, limit=30)
    if not entries:
        return
    summary_list = "\n".join(
        f"- {e['entry_date']}: {e['title'] or e['body'][:60]}…"
        for e in entries
    )
    result = await agent.invoke(
        f"Write a warm weekly journal digest for these {len(entries)} entries from the past 7 days:\n\n"
        f"{summary_list}\n\n"
        f"Compose a short HTML email summarizing themes, highlights, and growth. Keep it personal and encouraging.",
        thread_id="weekly-digest",
    )
    subject = f"📓 Weekly Journal Digest — {date.today().strftime('%B %d, %Y')}"
    sent    = _send_email(subject, result.answer)
    if sent:
        data = _load_store()
        data["last_digest"] = datetime.now(timezone.utc).isoformat()
        _save_store(data)
    log.info("Weekly digest sent: %s", sent)


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


class DigestConfigReq(BaseModel):
    enabled: bool = False


# ---------------------------------------------------------------------------
# Web app
# ---------------------------------------------------------------------------

def _web(port: int) -> None:
    import uvicorn
    from store import init_db, list_entries, list_dates

    init_db()
    agent = make_agent()

    app = FastAPI(title="Voice Journal")
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(_inbox_watcher(agent))
        log.info("Inbox watcher started.")

    @app.post("/ask")
    async def api_ask(req: AskReq):
        try:
            result = await agent.invoke(req.question, thread_id="chat")
            return {"answer": result.answer}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/upload")
    async def api_upload(file: UploadFile = File(...)):
        inbox = _DIR / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest    = inbox / file.filename
        content = await file.read()
        dest.write_bytes(content)
        return {"ok": True, "filename": file.filename,
                "message": "File queued — will be processed within the poll interval."}

    @app.get("/entries")
    async def api_entries(limit: int = 30):
        return list_entries(limit=limit)

    @app.get("/entries/dates")
    async def api_dates():
        return list_dates()

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

    @app.post("/settings/digest")
    async def api_digest(req: DigestConfigReq):
        data = _load_store()
        data["weekly_digest_enabled"] = req.enabled
        _save_store(data)
        return {"ok": True}

    @app.post("/digest/trigger")
    async def api_trigger_digest():
        asyncio.create_task(_send_weekly_digest(agent, _load_store()))
        return {"ok": True, "message": "Weekly digest triggered."}

    @app.get("/", response_class=HTMLResponse)
    async def ui():
        return HTMLResponse(_HTML)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice Journal</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:#0f1117;color:#e2e8f0;min-height:100vh}

  header{background:#1a1a2e;border-bottom:1px solid #2d2d4a;padding:14px 28px;
    display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10}
  header h1{font-size:16px;font-weight:700;color:#fff}
  .badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
  .badge-purple{background:#2e1065;color:#c4b5fd}
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

  .drop-zone{border:2px dashed #374151;border-radius:8px;padding:20px 14px;
    text-align:center;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
  .drop-zone:hover,.drop-zone.drag-over{border-color:#7c3aed;background:rgba(124,58,237,.08)}
  .drop-zone input[type=file]{position:absolute;inset:0;width:100%;height:100%;
    opacity:0;cursor:pointer;z-index:2}
  .dz-icon{font-size:28px;margin-bottom:6px}
  .drop-zone p{font-size:12px;color:#9ca3af}
  .drop-zone small{font-size:11px;color:#4b5563}

  .srow{display:flex;align-items:center;gap:8px;margin-bottom:9px}
  .srow label{font-size:12px;color:#9ca3af;min-width:90px}
  input[type=text],input[type=password],input[type=email],textarea{flex:1;
    padding:5px 9px;border-radius:5px;font-size:12px;background:#0f1117;
    border:1px solid #374151;color:#e2e8f0;outline:none}
  textarea{resize:vertical;min-height:80px;font-family:inherit}
  input:focus,textarea:focus{border-color:#7c3aed}
  .btn{padding:5px 14px;border-radius:6px;font-size:12px;font-weight:500;
    cursor:pointer;border:none;background:#7c3aed;color:#fff;transition:background .15s}
  .btn:hover{background:#6d28d9}
  .btn:disabled{background:#374151;color:#6b7280;cursor:default}
  .btn-sm{padding:3px 10px;font-size:11px}
  .btn-ghost{background:#1f2937;border:1px solid #374151;color:#9ca3af}
  .btn-ghost:hover{background:#374151}
  .save-ok{color:#4ade80;font-size:11px;margin-left:6px;display:none}

  .chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:11px}
  .chip{padding:4px 10px;border-radius:12px;font-size:11px;background:#1f2937;
    border:1px solid #374151;color:#9ca3af;cursor:pointer;transition:all .15s}
  .chip:hover{background:#7c3aed;border-color:#7c3aed;color:#fff}
  .chat-row{display:flex;gap:8px}
  .chat-input{flex:1;padding:8px 12px;border-radius:7px;font-size:13px;
    background:#0f1117;border:1px solid #374151;color:#e2e8f0;outline:none}
  .chat-input:focus{border-color:#7c3aed}
  .chat-send{padding:8px 16px;border-radius:7px;font-size:13px;cursor:pointer;
    border:none;background:#7c3aed;color:#fff}
  .chat-send:hover{background:#6d28d9}
  .chat-send:disabled{background:#374151;color:#6b7280;cursor:default}
  .chat-result{margin-top:12px;padding:12px;border-radius:7px;background:#0f1117;
    border:1px solid #2d2d4a;font-size:13px;line-height:1.6;color:#d1d5db;
    white-space:pre-wrap;display:none}
  .chat-result.vis{display:block}

  /* Entry timeline */
  .entry-item{border:1px solid #2d2d4a;border-radius:7px;margin-bottom:10px}
  .entry-header{padding:10px 14px;display:flex;align-items:center;gap:8px;
    cursor:pointer}
  .entry-header:hover{background:#1f2937;border-radius:7px 7px 0 0}
  .entry-title{font-size:12px;font-weight:600;color:#c5cae9;flex:1;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .entry-date{font-size:10px;color:#6b7280}
  .entry-source{font-size:10px;padding:1px 6px;border-radius:8px}
  .src-voice{background:#2e1065;color:#c4b5fd}
  .src-text{background:#1e3a5f;color:#60a5fa}
  .src-upload{background:#1c2e1c;color:#86efac}
  .entry-tags{font-size:10px;color:#4b5563}
  .entry-body{padding:10px 14px;font-size:12px;line-height:1.6;color:#d1d5db;
    white-space:pre-wrap;border-top:1px solid #2d2d4a;background:#0f1117;display:none}
  .entry-body.open{display:block}
  .empty-state{font-size:13px;color:#4b5563;text-align:center;padding:32px}

  .quick-write-area{width:100%;padding:10px;border-radius:7px;font-size:13px;
    background:#0f1117;border:1px solid #374151;color:#e2e8f0;outline:none;
    resize:vertical;min-height:80px;font-family:inherit;line-height:1.5}
  .quick-write-area:focus{border-color:#7c3aed}
</style>
</head>
<body>

<header>
  <h1>📓 Voice Journal</h1>
  <span class="badge badge-purple" id="entry-count">0 entries</span>
  <div class="spacer"></div>
  <span class="hdr-stat" id="hdr-stat">Inbox watcher active</span>
</header>

<div class="layout">

  <!-- ── Left ─────────────────────────────────────────── -->
  <div>

    <!-- Quick write -->
    <div class="card">
      <div class="card-header"><h2>✏️ Quick Entry</h2></div>
      <div class="card-body">
        <textarea class="quick-write-area" id="quick-text"
          placeholder="Write a journal entry… or just stream of consciousness. The agent will structure it."
          onkeydown="if(event.ctrlKey&&event.key==='Enter')saveQuick()"></textarea>
        <button class="btn btn-sm" style="margin-top:8px" onclick="saveQuick()" id="quick-btn">Save Entry</button>
        <span class="save-ok" id="quick-ok">✓ Saved</span>
      </div>
    </div>

    <!-- Upload -->
    <div class="card">
      <div class="card-header"><h2>🎤 Upload Audio or File</h2></div>
      <div class="card-body">
        <div class="drop-zone" id="drop-zone"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="handleDrop(event)">
          <input type="file" id="file-input" accept=".m4a,.mp3,.wav,.ogg,.flac,.txt,.md"
                 onchange="uploadFile(this.files[0])">
          <div class="dz-icon">🎙️</div>
          <p>Drop audio or text file, or click to upload</p>
          <small>.m4a · .mp3 · .wav · .txt · .md</small>
        </div>
        <div id="upload-status" style="font-size:12px;margin-top:8px;display:none"></div>
      </div>
    </div>

    <!-- Email settings -->
    <div class="card">
      <div class="card-header"><h2>✉️ Weekly Digest</h2></div>
      <div class="card-body">
        <div class="srow"><label>SMTP host</label>
          <input type="text" id="smtp-host" placeholder="smtp.gmail.com"></div>
        <div class="srow"><label>Username</label>
          <input type="email" id="smtp-user" placeholder="you@gmail.com"></div>
        <div class="srow"><label>Password</label>
          <input type="password" id="smtp-pass" placeholder="app password"></div>
        <div class="srow"><label>Digest to</label>
          <input type="email" id="smtp-to" placeholder="recipient@example.com"></div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <input type="checkbox" id="digest-enabled">
          <label for="digest-enabled" style="font-size:12px;color:#9ca3af;min-width:auto">
            Send weekly digest (every 7 days)
          </label>
        </div>
        <button class="btn btn-sm" onclick="saveEmail()">Save</button>
        <button class="btn btn-sm btn-ghost" style="margin-left:6px" onclick="triggerDigest()">Send now</button>
        <span class="save-ok" id="email-ok">✓ Saved</span>
      </div>
    </div>

  </div><!-- /left -->

  <!-- ── Right ─────────────────────────────────────────── -->
  <div>

    <!-- Chat -->
    <div class="card">
      <div class="card-header"><h2>💬 Ask About Your Journal</h2></div>
      <div class="card-body">
        <div class="chips">
          <span class="chip" onclick="ask(this.textContent)">What did I write about this week?</span>
          <span class="chip" onclick="ask(this.textContent)">Show my entries from yesterday</span>
          <span class="chip" onclick="ask(this.textContent)">What recurring themes do I have?</span>
          <span class="chip" onclick="ask(this.textContent)">How have I been feeling lately?</span>
          <span class="chip" onclick="ask(this.textContent)">Summarize last month's entries</span>
          <span class="chip" onclick="ask(this.textContent)">What goals did I mention?</span>
          <span class="chip" onclick="ask(this.textContent)">Show entries tagged work</span>
          <span class="chip" onclick="ask(this.textContent)">What was I grateful for recently?</span>
          <span class="chip" onclick="ask(this.textContent)">Any important decisions I noted?</span>
          <span class="chip" onclick="ask(this.textContent)">Write a reflection on this month</span>
        </div>
        <div class="chat-row">
          <input class="chat-input" id="chat-input" type="text"
            placeholder="Ask about your journal or add a new entry…"
            onkeydown="if(event.key==='Enter')ask()">
          <button class="chat-send" id="chat-send" onclick="ask()">Ask</button>
        </div>
        <div class="chat-result" id="chat-result"></div>
      </div>
    </div>

    <!-- Entry timeline -->
    <div class="card">
      <div class="card-header">
        <h2>📅 Recent Entries</h2>
        <button class="btn btn-sm btn-ghost" style="margin-left:auto" onclick="loadEntries()">↺ Refresh</button>
      </div>
      <div class="card-body" id="timeline-body">
        <div class="empty-state">No entries yet — write one above or drop an audio file.</div>
      </div>
    </div>

  </div><!-- /right -->

</div>

<script>
async function init() {
  await loadSettings();
  await loadEntries();
  setInterval(loadEntries, 15000);
  updateWatcherStat();
  setInterval(updateWatcherStat, 10000);
}

async function updateWatcherStat() {
  try {
    const s = await fetch('/watcher/status').then(r => r.json());
    document.getElementById('hdr-stat').textContent =
      `Inbox watcher active · ${s.processed} files processed`;
  } catch(e) {}
}

async function loadSettings() {
  try {
    const s = await fetch('/settings').then(r => r.json());
    const e = s.email || {};
    document.getElementById('smtp-host').value     = e.host     || '';
    document.getElementById('smtp-user').value     = e.user     || '';
    document.getElementById('smtp-pass').value     = e.password ? '••••••••' : '';
    document.getElementById('smtp-to').value       = e.to       || '';
    document.getElementById('digest-enabled').checked = !!s.weekly_digest_enabled;
  } catch(e) {}
}

async function loadEntries() {
  try {
    const entries = await fetch('/entries?limit=50').then(r => r.json());
    document.getElementById('entry-count').textContent = entries.length + ' entries';
    renderTimeline(entries);
  } catch(e) {}
}

function renderTimeline(entries) {
  const body = document.getElementById('timeline-body');
  if (!entries.length) {
    body.innerHTML = '<div class="empty-state">No entries yet. Start by writing above!</div>';
    return;
  }
  body.innerHTML = entries.map((e, i) => {
    const srcCls = e.source === 'voice' ? 'src-voice' : e.source === 'upload' ? 'src-upload' : 'src-text';
    const tags   = e.tags ? e.tags.split(',').filter(Boolean).map(t =>
      `<span style="color:#6b7280">#${esc(t.trim())}</span>`).join(' ') : '';
    return `
      <div class="entry-item">
        <div class="entry-header" onclick="toggleEntry('eb-${i}','ei-${i}')">
          <span class="entry-title">${esc(e.title || '(untitled)')}</span>
          <span class="entry-source ${srcCls}">${e.source}</span>
          <span class="entry-date">${e.entry_date}</span>
          <span id="ei-${i}" style="font-size:11px;color:#4b5563;margin-left:4px">▸</span>
        </div>
        ${tags ? `<div style="padding:0 14px 6px;font-size:10px">${tags}</div>` : ''}
        <div class="entry-body" id="eb-${i}">${esc(e.body)}</div>
      </div>`;
  }).join('');
}

function toggleEntry(bodyId, iconId) {
  document.getElementById(bodyId).classList.toggle('open');
  const icon = document.getElementById(iconId);
  icon.textContent = document.getElementById(bodyId).classList.contains('open') ? '▾' : '▸';
}

async function saveQuick() {
  const text = document.getElementById('quick-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('quick-btn');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch('/ask', { method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ question: text + '\n\nFormat this as a journal entry and save it.' }) });
    const d = await r.json();
    document.getElementById('quick-ok').style.display = 'inline';
    setTimeout(() => document.getElementById('quick-ok').style.display = 'none', 2000);
    document.getElementById('quick-text').value = '';
    await loadEntries();
  } catch(e) { alert('Error: ' + e.message); }
  btn.disabled = false; btn.textContent = 'Save Entry';
}

async function uploadFile(file) {
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  const status = document.getElementById('upload-status');
  status.style.display = 'block';
  status.textContent = `Uploading ${file.name}…`;
  try {
    const r = await fetch('/upload', { method:'POST', body: fd });
    const d = await r.json();
    status.textContent = `✓ ${d.message}`;
    setTimeout(() => { status.style.display = 'none'; loadEntries(); }, 3000);
  } catch(e) { status.textContent = 'Error: ' + e.message; }
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
    const r = await fetch('/ask', { method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ question: q }) });
    const d = await r.json();
    res.textContent = d.answer || d.error || '(no response)';
    await loadEntries();
  } catch(e) { res.textContent = 'Error: ' + e.message; }
  btn.disabled = false; btn.textContent = 'Ask';
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
  await fetch('/settings/digest', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled: document.getElementById('digest-enabled').checked }) });
  const ok = document.getElementById('email-ok');
  ok.style.display = 'inline';
  setTimeout(() => ok.style.display = 'none', 2000);
}

async function triggerDigest() {
  await fetch('/digest/trigger', { method:'POST' });
  alert('Weekly digest queued!');
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
    parser = argparse.ArgumentParser(description="Voice Journal — web UI")
    parser.add_argument("--port",     type=int, default=18799)
    parser.add_argument("--provider", "-p", default=None,
        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",    "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    print(f"\n  Voice Journal  →  http://127.0.0.1:{args.port}\n")
    _web(args.port)
