"""
Document Intelligence — inbox watcher + RAG corpus + browser chat
=================================================================

Drop PDFs or images into the inbox folder. The background watcher extracts text
with docling, indexes it into ChromaDB, and the agent can search the full corpus.

Tools:
  - extract_document  : structured field extraction (invoices, CVs, contracts)
  - enrich_document   : AI summary + keywords + entities
  - search_corpus     : RAG search over all indexed documents (ChromaDB)

Email alerts: configure keywords — if an indexed document matches, get notified.

Run:
    python main.py
    python main.py --port 18797
    python main.py --provider anthropic

Then open: http://127.0.0.1:18797

Environment variables:
    LLM_PROVIDER      rits | anthropic | openai | ollama | watsonx | litellm
    LLM_MODEL         model override
    POLL_SECONDS      inbox poll interval (default: 15)
    SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD / ALERT_TO   email settings

Required:
    pip install docling chromadb
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
from typing import Optional

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
# Persistent store (.store.json)
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


# ---------------------------------------------------------------------------
# SQLite document log
# ---------------------------------------------------------------------------

_DB_PATH = _DIR / "intel.db"


def _init_db() -> None:
    con = sqlite3.connect(_DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            doc_type    TEXT,
            summary     TEXT,
            report      TEXT,
            source      TEXT DEFAULT 'watcher',
            alerted     INTEGER DEFAULT 0,
            indexed     INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def _log_document(filename: str, doc_type: str, summary: str, report: str,
                  source: str = "watcher", alerted: bool = False,
                  indexed: bool = False) -> str:
    doc_id = uuid.uuid4().hex[:12]
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
        (doc_id, filename, doc_type, summary, report, source,
         1 if alerted else 0, 1 if indexed else 0,
         datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()
    return doc_id


def _list_documents(limit: int = 50) -> list[dict]:
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _corpus_stats() -> dict:
    con = sqlite3.connect(_DB_PATH)
    row = con.execute(
        "SELECT COUNT(*) as total, SUM(indexed) as indexed FROM documents"
    ).fetchone()
    con.close()
    return {"total": row[0] or 0, "indexed": row[1] or 0}


# ---------------------------------------------------------------------------
# ChromaDB vector store
# ---------------------------------------------------------------------------

_CHROMA_PATH = _DIR / ".chroma"
_COLLECTION_NAME = "doc_intel"
_chroma_client = None
_chroma_collection = None


def _get_chroma():
    global _chroma_client, _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection
    try:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _chroma_collection = _chroma_client.get_or_create_collection(
            _COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        return _chroma_collection
    except ImportError:
        log.warning("chromadb not installed — RAG search disabled. Run: pip install chromadb")
        return None


def _index_document(doc_id: str, filename: str, text: str) -> bool:
    """Chunk text and index into ChromaDB. Returns True on success."""
    col = _get_chroma()
    if col is None:
        return False
    try:
        # Split into ~512-char chunks with overlap
        chunk_size = 512
        overlap = 64
        chunks = []
        i = 0
        while i < len(text):
            chunks.append(text[i:i + chunk_size])
            i += chunk_size - overlap
        if not chunks:
            return False

        ids = [f"{doc_id}_chunk{j}" for j in range(len(chunks))]
        metadatas = [{"doc_id": doc_id, "filename": filename, "chunk": j}
                     for j in range(len(chunks))]
        col.add(documents=chunks, ids=ids, metadatas=metadatas)
        return True
    except Exception as e:
        log.warning("ChromaDB indexing failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(subject: str, body: str, cfg: dict) -> bool:
    host = cfg.get("smtp_host", "")
    user = cfg.get("smtp_user", "")
    pwd  = cfg.get("smtp_pass", "")
    to   = cfg.get("alert_to", "")
    if not all([host, user, pwd, to]):
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = to
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL(host, 465, timeout=10) as s:
            s.login(user, pwd)
            s.sendmail(user, [to], msg.as_string())
        return True
    except Exception as e:
        log.warning("Email failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

def _make_tools():
    from langchain_core.tools import tool

    @tool
    def extract_document(file_path: str, extraction_hint: str = "") -> str:
        """
        Extract structured fields from a PDF or image using docling.
        Best for: invoices, CVs, forms, receipts, contracts.

        Args:
            file_path:        Absolute path to the PDF or image.
            extraction_hint:  Optional hint, e.g. "invoice fields: number, vendor, total, date"
        """
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(file_path)
            text = result.document.export_to_markdown()
            hint = f"\n\nExtraction focus: {extraction_hint}" if extraction_hint else ""
            return text + hint
        except ImportError:
            return '{"error": "docling not installed. Run: pip install docling"}'
        except Exception as e:
            return f'{{"error": "{e}"}}'

    @tool
    def enrich_document(file_path: str) -> str:
        """
        Extract text and enrich with AI summary, keywords, and entities.
        Best for: reports, papers, articles, meeting notes.

        Args:
            file_path: Absolute path to the PDF or image.
        """
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(file_path)
            text = result.document.export_to_markdown()
            return text
        except ImportError:
            return '{"error": "docling not installed. Run: pip install docling"}'
        except Exception as e:
            return f'{{"error": "{e}"}}'

    @tool
    def search_corpus(query: str, n_results: int = 5) -> str:
        """
        Semantic search over all indexed documents in the corpus.
        Use this to answer questions that may span multiple documents.

        Args:
            query:     The search query or question.
            n_results: How many result chunks to return (default 5).
        """
        col = _get_chroma()
        if col is None:
            return '{"error": "ChromaDB not available. Run: pip install chromadb"}'
        try:
            results = col.query(query_texts=[query], n_results=min(n_results, col.count() or 1))
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            if not docs:
                return "No results found in the document corpus."
            parts = []
            for doc_text, meta in zip(docs, metas):
                parts.append(
                    f"[{meta.get('filename', 'unknown')} chunk {meta.get('chunk', '?')}]\n{doc_text}"
                )
            return "\n\n---\n\n".join(parts)
        except Exception as e:
            return f'{{"error": "{e}"}}'

    return [extract_document, enrich_document, search_corpus]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def _make_agent():
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
# Background watcher
# ---------------------------------------------------------------------------

async def _inbox_watcher(agent) -> None:
    inbox = _DIR / "inbox"
    processed = inbox / "processed"
    inbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    while True:
        cfg = _load_store()
        interval = int(cfg.get("poll_seconds", 15))
        try:
            files = [
                f for f in inbox.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
            for fpath in files:
                dest = processed / fpath.name
                try:
                    shutil.move(str(fpath), str(dest))
                except Exception as e:
                    log.warning("Move failed: %s", e)
                    continue

                log.info("[watcher] processing %s", fpath.name)
                try:
                    result = await agent.invoke(
                        f"A new document has landed: {dest}\n\n"
                        f"Classify it, call the appropriate tool (extract_document or enrich_document), "
                        f"and produce a structured intelligence report. "
                        f"Also call search_corpus if the user later asks questions.",
                        thread_id=f"doc-{fpath.stem}",
                    )
                    report = result.answer
                except Exception as e:
                    log.error("[watcher] agent error: %s", e)
                    report = f"Error: {e}"

                # Extract text for RAG indexing
                try:
                    from docling.document_converter import DocumentConverter
                    text = DocumentConverter().convert(str(dest)).document.export_to_markdown()
                    indexed = _index_document(fpath.stem, fpath.name, text)
                except Exception:
                    indexed = False

                # Classify doc type from report
                doc_type = "Other"
                rl = report.lower()
                if any(w in rl for w in ["invoice", "receipt"]):
                    doc_type = "Invoice"
                elif any(w in rl for w in ["cv", "resume", "curriculum"]):
                    doc_type = "CV"
                elif any(w in rl for w in ["contract", "agreement", "nda"]):
                    doc_type = "Contract"
                elif any(w in rl for w in ["report", "paper", "article", "study"]):
                    doc_type = "Report"
                elif any(w in rl for w in ["meeting", "notes", "minutes"]):
                    doc_type = "Meeting Notes"

                # Summary = first non-empty line after "### Summary"
                summary = ""
                lines = report.splitlines()
                in_summary = False
                for line in lines:
                    if "### Summary" in line:
                        in_summary = True
                        continue
                    if in_summary and line.strip():
                        summary = line.strip()[:200]
                        break
                if not summary and lines:
                    summary = lines[0][:200]

                # Keyword alert
                keywords = [k.strip().lower() for k in cfg.get("alert_keywords", []) if k.strip()]
                alerted = False
                if keywords and any(k in report.lower() for k in keywords):
                    subject = f"[Doc Intel] Keyword alert — {fpath.name}"
                    sent = _send_email(subject, report, cfg)
                    alerted = sent
                    if sent:
                        log.info("[watcher] alert email sent for %s", fpath.name)

                _log_document(
                    filename=fpath.name,
                    doc_type=doc_type,
                    summary=summary,
                    report=report,
                    source="watcher",
                    alerted=alerted,
                    indexed=indexed,
                )
        except Exception as e:
            log.error("[watcher] error: %s", e)

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


class SettingsRequest(BaseModel):
    poll_seconds: Optional[int] = None
    alert_keywords: Optional[list[str]] = None
    smtp_host: Optional[str] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    alert_to: Optional[str] = None


_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    _init_db()
    _get_chroma()  # warm up
    _agent = _make_agent()
    asyncio.create_task(_inbox_watcher(_agent))
    yield


app = FastAPI(title="Document Intelligence", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_HTML)


@app.post("/chat")
async def chat(req: ChatRequest):
    if _agent is None:
        raise HTTPException(503, "Agent not ready")
    thread = req.thread_id or "main"
    try:
        result = await _agent.invoke(req.message, thread_id=thread)
        return {"answer": result.answer, "thread_id": thread}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    fname = file.filename or f"upload_{uuid.uuid4().hex[:8]}"
    dest = _DIR / "inbox" / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    return {"status": "queued", "filename": fname}


@app.get("/documents")
async def documents():
    return _list_documents(50)


@app.get("/stats")
async def stats():
    s = _corpus_stats()
    col = _get_chroma()
    s["chroma_chunks"] = col.count() if col else 0
    return s


@app.get("/settings")
async def get_settings():
    cfg = _load_store()
    cfg.pop("smtp_pass", None)
    return cfg


@app.post("/settings")
async def save_settings(req: SettingsRequest):
    cfg = _load_store()
    if req.poll_seconds is not None:
        cfg["poll_seconds"] = req.poll_seconds
    if req.alert_keywords is not None:
        cfg["alert_keywords"] = req.alert_keywords
    if req.smtp_host is not None:
        cfg["smtp_host"] = req.smtp_host
    if req.smtp_user is not None:
        cfg["smtp_user"] = req.smtp_user
    if req.smtp_pass is not None:
        cfg["smtp_pass"] = req.smtp_pass
    if req.alert_to is not None:
        cfg["alert_to"] = req.alert_to
    _save_store(cfg)
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Inline HTML UI
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Document Intelligence</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;height:100vh;display:flex;flex-direction:column}
  header{background:#1a1a2e;padding:14px 24px;display:flex;align-items:center;gap:14px;border-bottom:1px solid #2d2d4e}
  header h1{font-size:1.15rem;font-weight:600;color:#e2e8f0}
  header .badge{background:#7c3aed;color:#fff;font-size:.7rem;padding:2px 8px;border-radius:9px;font-weight:700}
  .stats-bar{display:flex;gap:16px;padding:8px 24px;background:#12121f;border-bottom:1px solid #2d2d4e;font-size:.78rem;color:#94a3b8}
  .stats-bar span b{color:#a78bfa}
  .layout{display:grid;grid-template-columns:320px 1fr;flex:1;min-height:0}
  /* LEFT PANEL */
  .left{background:#12121f;border-right:1px solid #2d2d4e;display:flex;flex-direction:column;overflow-y:auto}
  .panel{padding:16px;border-bottom:1px solid #2d2d4e}
  .panel h3{font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#7c3aed;margin-bottom:10px}
  /* Drop zone */
  .dropzone{border:2px dashed #4c1d95;border-radius:10px;padding:22px;text-align:center;cursor:pointer;transition:all .2s;color:#a78bfa;position:relative;overflow:hidden}
  .dropzone:hover,.dropzone.drag{border-color:#7c3aed;background:#1e1040}
  .dropzone input[type=file]{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;z-index:2}
  .dropzone .icon{font-size:2rem;margin-bottom:6px}
  .dropzone p{font-size:.8rem;color:#94a3b8}
  /* Settings */
  label{display:block;font-size:.75rem;color:#94a3b8;margin-bottom:2px;margin-top:8px}
  input,textarea{width:100%;background:#1e1e3f;border:1px solid #3d3d6b;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:.82rem}
  input:focus,textarea:focus{outline:none;border-color:#7c3aed}
  textarea{resize:vertical;min-height:56px}
  .btn{width:100%;padding:8px;border-radius:7px;border:none;cursor:pointer;font-size:.82rem;font-weight:600;margin-top:10px}
  .btn-violet{background:#7c3aed;color:#fff}
  .btn-violet:hover{background:#6d28d9}
  .btn-sm{padding:5px 12px;font-size:.75rem;border-radius:6px;border:none;cursor:pointer;font-weight:600}
  .btn-outline{background:transparent;border:1px solid #3d3d6b;color:#94a3b8}
  .btn-outline:hover{border-color:#7c3aed;color:#a78bfa}
  /* Log */
  .doc-list{display:flex;flex-direction:column;gap:8px}
  .doc-card{background:#1a1a2e;border:1px solid #2d2d4e;border-radius:8px;padding:10px;cursor:pointer;transition:.15s}
  .doc-card:hover{border-color:#7c3aed}
  .doc-card .dc-name{font-size:.8rem;font-weight:600;color:#c4b5fd;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .doc-card .dc-meta{display:flex;gap:6px;margin-top:4px;flex-wrap:wrap}
  .tag{font-size:.68rem;padding:1px 7px;border-radius:9px;font-weight:600}
  .tag-invoice{background:#1e3a5f;color:#60a5fa}
  .tag-cv{background:#1a3a2e;color:#34d399}
  .tag-report{background:#3a2a1a;color:#fb923c}
  .tag-contract{background:#3a1a3a;color:#e879f9}
  .tag-meeting{background:#1a2a3a;color:#7dd3fc}
  .tag-other{background:#2a2a2a;color:#94a3b8}
  .tag-indexed{background:#1e3a1a;color:#4ade80}
  .tag-alerted{background:#3a1a1a;color:#f87171}
  .dc-summary{font-size:.73rem;color:#94a3b8;margin-top:4px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  /* RIGHT PANEL */
  .right{display:flex;flex-direction:column;min-height:0}
  .chat-area{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
  .msg{max-width:78%;padding:11px 15px;border-radius:12px;font-size:.87rem;line-height:1.55;word-break:break-word}
  .msg-user{align-self:flex-end;background:#4c1d95;color:#ede9fe;border-bottom-right-radius:4px}
  .msg-bot{align-self:flex-start;background:#1a1a2e;border:1px solid #2d2d4e;border-bottom-left-radius:4px}
  .msg-bot pre{background:#0f1117;padding:8px;border-radius:6px;overflow-x:auto;font-size:.8rem;margin-top:6px}
  .chips{display:flex;flex-wrap:wrap;gap:6px;padding:12px 20px;border-top:1px solid #1a1a2e}
  .chip{background:#1a1a2e;border:1px solid #3d3d6b;border-radius:16px;padding:5px 12px;font-size:.75rem;color:#a78bfa;cursor:pointer;transition:.15s;white-space:nowrap}
  .chip:hover{background:#2d1f5e;border-color:#7c3aed}
  .input-bar{display:flex;gap:10px;padding:14px 20px;background:#12121f;border-top:1px solid #2d2d4e}
  .input-bar input{flex:1;background:#1e1e3f;border:1px solid #3d3d6b;border-radius:8px;padding:9px 14px;color:#e2e8f0;font-size:.9rem}
  .input-bar input:focus{outline:none;border-color:#7c3aed}
  .send-btn{background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:9px 18px;cursor:pointer;font-weight:700;font-size:.9rem}
  .send-btn:hover{background:#6d28d9}
  .modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}
  .modal-overlay.open{display:flex}
  .modal{background:#1a1a2e;border:1px solid #3d3d6b;border-radius:12px;padding:24px;max-width:640px;width:90%;max-height:80vh;overflow-y:auto}
  .modal h3{color:#a78bfa;margin-bottom:12px}
  .modal pre{background:#0f1117;padding:12px;border-radius:8px;font-size:.78rem;white-space:pre-wrap;word-break:break-word}
  .modal-close{float:right;background:none;border:none;color:#94a3b8;font-size:1.2rem;cursor:pointer}
  .spinner{display:inline-block;width:16px;height:16px;border:2px solid #4c1d95;border-top-color:#a78bfa;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px}
  @keyframes spin{to{transform:rotate(360deg)}}
  ::-webkit-scrollbar{width:5px;height:5px}
  ::-webkit-scrollbar-track{background:#0f1117}
  ::-webkit-scrollbar-thumb{background:#3d3d6b;border-radius:3px}
</style>
</head>
<body>
<header>
  <div style="font-size:1.5rem">🧠</div>
  <h1>Document Intelligence</h1>
  <span class="badge">RAG + ChromaDB</span>
  <div style="margin-left:auto;display:flex;gap:8px">
    <button class="btn-sm btn-outline" onclick="refreshStats()">↻ Refresh</button>
  </div>
</header>
<div class="stats-bar" id="statsBar">
  <span>Documents: <b id="statTotal">—</b></span>
  <span>Indexed: <b id="statIndexed">—</b></span>
  <span>Corpus chunks: <b id="statChunks">—</b></span>
</div>
<div class="layout">
  <!-- LEFT -->
  <div class="left">
    <!-- Upload -->
    <div class="panel">
      <h3>📄 Upload Document</h3>
      <div class="dropzone" id="dropzone"
           ondragover="event.preventDefault();this.classList.add('drag')"
           ondragleave="this.classList.remove('drag')"
           ondrop="handleDrop(event)">
        <input type="file" id="fileInput" accept=".pdf,.png,.jpg,.jpeg,.tiff,.bmp"
               onchange="handleFile(this.files[0])"/>
        <div class="icon">📎</div>
        <p>Drop PDF / image here<br>or click to browse</p>
      </div>
      <div id="uploadStatus" style="font-size:.75rem;margin-top:6px;display:none"></div>
    </div>

    <!-- Watcher settings -->
    <div class="panel">
      <h3>⚙️ Watcher Settings</h3>
      <label>Poll interval (seconds)</label>
      <input type="number" id="cfgPoll" value="15" min="5"/>
      <label>Alert keywords (comma-separated)</label>
      <input type="text" id="cfgKeywords" placeholder="urgent, confidential, overdue"/>
      <button class="btn btn-violet" onclick="saveSettings()">Save Settings</button>
    </div>

    <!-- Email -->
    <div class="panel">
      <h3>✉️ Email Alerts</h3>
      <label>SMTP Host</label>
      <input type="text" id="cfgSmtpHost" placeholder="smtp.gmail.com"/>
      <label>Username</label>
      <input type="text" id="cfgSmtpUser" placeholder="you@gmail.com"/>
      <label>Password / App password</label>
      <input type="password" id="cfgSmtpPass" placeholder="••••••••"/>
      <label>Alert recipient</label>
      <input type="text" id="cfgAlertTo" placeholder="alerts@company.com"/>
      <button class="btn btn-violet" onclick="saveSettings()">Save Email Config</button>
    </div>

    <!-- Document log -->
    <div class="panel" style="flex:1">
      <h3>📚 Document Corpus</h3>
      <div class="doc-list" id="docList"><p style="color:#94a3b8;font-size:.78rem">No documents yet.</p></div>
    </div>
  </div>

  <!-- RIGHT: chat -->
  <div class="right">
    <div class="chat-area" id="chatArea">
      <div class="msg msg-bot">
        <b>Document Intelligence</b><br><br>
        Drop PDFs and images into the inbox — I'll extract, classify, and index them into a searchable corpus.<br><br>
        Ask me anything about your documents:
        <ul style="margin-top:8px;padding-left:20px;color:#94a3b8;font-size:.85rem">
          <li>Search across all indexed files</li>
          <li>Extract structured fields from invoices/CVs</li>
          <li>Summarize reports and articles</li>
          <li>Compare information across documents</li>
        </ul>
      </div>
    </div>
    <div class="chips">
      <button class="chip" onclick="sendChip(this)">What documents are in my corpus?</button>
      <button class="chip" onclick="sendChip(this)">Search for budget figures</button>
      <button class="chip" onclick="sendChip(this)">Find any action items or deadlines</button>
      <button class="chip" onclick="sendChip(this)">What contracts have been indexed?</button>
      <button class="chip" onclick="sendChip(this)">Summarize all reports</button>
      <button class="chip" onclick="sendChip(this)">Extract all vendor names from invoices</button>
      <button class="chip" onclick="sendChip(this)">Find mentions of payment terms</button>
      <button class="chip" onclick="sendChip(this)">What skills are in the CVs?</button>
      <button class="chip" onclick="sendChip(this)">Any documents mentioning compliance?</button>
      <button class="chip" onclick="sendChip(this)">Search for contact information</button>
    </div>
    <div class="input-bar">
      <input type="text" id="chatInput" placeholder="Ask anything about your documents…" onkeydown="if(event.key==='Enter')sendMessage()"/>
      <button class="send-btn" onclick="sendMessage()">Send</button>
    </div>
  </div>
</div>

<!-- Modal for full report -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <button class="modal-close" onclick="closeModal()">✕</button>
    <h3 id="modalTitle">Document Report</h3>
    <pre id="modalBody"></pre>
  </div>
</div>

<script>
const threadId = 'intel-' + Math.random().toString(36).slice(2,8);

function typeTag(t) {
  const m = {Invoice:'tag-invoice',CV:'tag-cv',Report:'tag-report',Contract:'tag-contract','Meeting Notes':'tag-meeting'};
  return m[t] || 'tag-other';
}

async function refreshStats() {
  const s = await fetch('/stats').then(r=>r.json());
  document.getElementById('statTotal').textContent = s.total;
  document.getElementById('statIndexed').textContent = s.indexed;
  document.getElementById('statChunks').textContent = s.chroma_chunks ?? '—';
}

async function refreshDocs() {
  const docs = await fetch('/documents').then(r=>r.json());
  const el = document.getElementById('docList');
  if(!docs.length){el.innerHTML='<p style="color:#94a3b8;font-size:.78rem">No documents yet.</p>';return;}
  el.innerHTML = docs.map(d=>`
    <div class="doc-card" onclick="showReport(${JSON.stringify(d.filename).replace(/'/g,"&#39;")},${JSON.stringify(d.report||'').replace(/'/g,"&#39;")})">
      <div class="dc-name">${d.filename}</div>
      <div class="dc-meta">
        <span class="tag ${typeTag(d.doc_type)}">${d.doc_type||'Other'}</span>
        ${d.indexed?'<span class="tag tag-indexed">indexed</span>':''}
        ${d.alerted?'<span class="tag tag-alerted">alerted</span>':''}
      </div>
      <div class="dc-summary">${d.summary||''}</div>
    </div>
  `).join('');
}

function showReport(filename, report) {
  document.getElementById('modalTitle').textContent = filename;
  document.getElementById('modalBody').textContent = report || '(no report)';
  document.getElementById('modalOverlay').classList.add('open');
}
function closeModal() {
  document.getElementById('modalOverlay').classList.remove('open');
}

async function loadSettings() {
  const cfg = await fetch('/settings').then(r=>r.json());
  if(cfg.poll_seconds) document.getElementById('cfgPoll').value = cfg.poll_seconds;
  if(cfg.alert_keywords) document.getElementById('cfgKeywords').value = (cfg.alert_keywords||[]).join(', ');
  if(cfg.smtp_host) document.getElementById('cfgSmtpHost').value = cfg.smtp_host;
  if(cfg.smtp_user) document.getElementById('cfgSmtpUser').value = cfg.smtp_user;
  if(cfg.alert_to) document.getElementById('cfgAlertTo').value = cfg.alert_to;
}

async function saveSettings() {
  const keywords = document.getElementById('cfgKeywords').value
    .split(',').map(k=>k.trim()).filter(Boolean);
  await fetch('/settings', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      poll_seconds: parseInt(document.getElementById('cfgPoll').value)||15,
      alert_keywords: keywords,
      smtp_host: document.getElementById('cfgSmtpHost').value.trim(),
      smtp_user: document.getElementById('cfgSmtpUser').value.trim(),
      smtp_pass: document.getElementById('cfgSmtpPass').value,
      alert_to:  document.getElementById('cfgAlertTo').value.trim(),
    })
  });
  const btn = event.target;
  btn.textContent = '✓ Saved';
  setTimeout(()=>btn.textContent='Save Settings',1800);
}

async function handleFile(file) {
  if(!file) return;
  const fd = new FormData();
  fd.append('file', file);
  const st = document.getElementById('uploadStatus');
  st.style.display = 'block';
  st.textContent = '⏳ Uploading…';
  const r = await fetch('/upload', {method:'POST', body:fd}).then(r=>r.json());
  st.textContent = r.status === 'queued'
    ? `✓ ${r.filename} queued for processing`
    : `✗ Error: ${JSON.stringify(r)}`;
  setTimeout(()=>{st.style.display='none';}, 4000);
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('dropzone').classList.remove('drag');
  const file = e.dataTransfer.files[0];
  if(file) handleFile(file);
}

function addMsg(role, text) {
  const chat = document.getElementById('chatArea');
  const div = document.createElement('div');
  div.className = 'msg msg-' + role;
  div.innerHTML = text.replace(/```([\s\S]*?)```/g,'<pre>$1</pre>').replace(/\n/g,'<br>');
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

async function sendMessage() {
  const inp = document.getElementById('chatInput');
  const msg = inp.value.trim();
  if(!msg) return;
  inp.value = '';
  addMsg('user', msg);
  const thinking = addMsg('bot', '<span class="spinner"></span>Thinking…');
  const r = await fetch('/chat',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message:msg, thread_id:threadId})
  }).then(r=>r.json()).catch(e=>({answer:'Error: '+e}));
  thinking.remove();
  addMsg('bot', r.answer || r.detail || 'No response');
  refreshDocs();
  refreshStats();
}

function sendChip(btn) {
  document.getElementById('chatInput').value = btn.textContent;
  sendMessage();
}

// Auto-refresh every 10s
setInterval(()=>{refreshDocs();refreshStats();}, 10000);
loadSettings();
refreshDocs();
refreshStats();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Document Intelligence — FastAPI + RAG")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18797)
    parser.add_argument("--provider", "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model", "-m", default=None)
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    import uvicorn
    print(f"\n  Document Intelligence  →  http://{args.host}:{args.port}\n")
    print("  Drop PDFs/images into ./inbox — they get extracted and indexed.\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
