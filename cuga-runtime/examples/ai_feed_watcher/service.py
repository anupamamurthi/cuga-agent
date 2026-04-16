"""
AI Feed Watcher — FastAPI service.

Fetches posts from RSS/Atom feeds covering AI research, blogs, and newsletters.
No API key required.

Endpoints
---------
GET  /          Serve the UI (index.html)
GET  /health    Liveness check
GET  /feeds     List configured RSS feeds
POST /fetch     Fetch recent posts from all (or selected) feeds
                  preview_only=true  → return posts, don't submit to cuga-runtime
                  preview_only=false → submit each post to cuga-runtime as an event

Configuration (environment variables)
--------------------------------------
CUGA_RUNTIME_URL   URL of the cuga-runtime service. Default: http://localhost:8001

Run
---
    uvicorn examples.ai_feed_watcher.service:app --port 8002 --reload
"""

import datetime
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from examples.ai_feed_watcher.watched_accounts import RSS_FEEDS, AI_RELEVANCE_PROMPT

_RUNTIME_URL = os.getenv("CUGA_RUNTIME_URL", "http://localhost:8001")
_UI_DIR = Path(__file__).parent / "ui"

app = FastAPI(
    title="AI Feed Watcher",
    description="Monitors AI/LLM RSS feeds and routes posts to CUGA for analysis",
    version="0.1.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/ui", StaticFiles(directory=_UI_DIR), name="ui")


# ── Request schema ────────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    sources: list[str] = Field(
        default_factory=lambda: [f["source"] for f in RSS_FEEDS],
        description="Feed source slugs to fetch. Defaults to all feeds.",
    )
    days: int = Field(7, ge=1, le=30, description="How many days back to include")
    preview_only: bool = Field(True, description="Return posts without submitting to cuga-runtime")
    runtime_url: Optional[str] = Field(None, description="Override cuga-runtime URL")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(_UI_DIR / "index.html")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "runtime_url": _RUNTIME_URL, "feeds": len(RSS_FEEDS)})


@app.get("/feeds")
async def list_feeds():
    return JSONResponse({"feeds": [{"source": f["source"], "name": f["name"]} for f in RSS_FEEDS]})


@app.post("/fetch")
async def fetch(req: FetchRequest):
    """
    Fetch recent posts from configured RSS/Atom feeds.
    No API key required — all feeds are publicly accessible.
    """
    runtime_url = req.runtime_url or _RUNTIME_URL

    # Filter to requested sources
    feeds_to_fetch = [f for f in RSS_FEEDS if f["source"] in req.sources]
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=req.days)

    posts, errors = await _fetch_rss_feeds(feeds_to_fetch, cutoff)

    if req.preview_only:
        return JSONResponse({
            "source": "rss",
            "posts": posts,
            "errors": errors,
            "feeds_fetched": len(feeds_to_fetch),
            "feeds_with_errors": len(errors),
            "prompt": AI_RELEVANCE_PROMPT,
        })

    # Submit to cuga-runtime
    queued = 0
    async with httpx.AsyncClient(timeout=10.0) as http:
        for p in posts:
            event = {
                "query": AI_RELEVANCE_PROMPT,
                "persona": "default",
                "trigger": f"rss.new_post.{p['source']}",
                "context": {
                    "source": p["source"],
                    "feed_name": p["feed_name"],
                    "post_id": p["post_id"],
                    "title": p["title"],
                    "text": p["text"],
                    "url": p.get("url", ""),
                    "posted_at": p.get("created_at", ""),
                },
            }
            r = await http.post(f"{runtime_url}/events", json=event)
            if r.status_code == 202:
                queued += 1

    return JSONResponse({
        "source": "rss",
        "queued": queued,
        "feeds_fetched": len(feeds_to_fetch),
        "feeds_with_errors": len(errors),
        "errors": errors,
    })


# ── Runtime proxy ────────────────────────────────────────────────────────────
# Forward /runtime/* to cuga-runtime so the UI only talks to one origin (8002).

@app.post("/runtime/events")
async def proxy_post_events(request: Request):
    body = await request.json()
    runtime_url = request.query_params.get("runtime_url") or _RUNTIME_URL
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.post(f"{runtime_url}/events", json=body)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/runtime/events")
async def proxy_get_events(request: Request):
    runtime_url = _RUNTIME_URL
    qs = str(request.url.query)
    url = f"{runtime_url}/events"
    if qs:
        url += f"?{qs}"
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.get(url)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/runtime/events/{task_id}")
async def proxy_get_event(task_id: str):
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.get(f"{_RUNTIME_URL}/events/{task_id}")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/runtime/health")
async def proxy_runtime_health():
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            r = await http.get(f"{_RUNTIME_URL}/health")
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"status": "unreachable", "error": str(e)}, status_code=503)


# ── RSS/Atom fetching ─────────────────────────────────────────────────────────

async def _fetch_rss_feeds(feeds: list[dict], cutoff: datetime.datetime) -> tuple[list[dict], list[str]]:
    posts = []
    errors = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
        for feed in feeds:
            try:
                r = await http.get(
                    feed["url"],
                    headers={"User-Agent": "Mozilla/5.0 (compatible; feed-reader/1.0)"},
                )
                if r.status_code != 200:
                    errors.append(f"{feed['source']}: HTTP {r.status_code}")
                    continue

                items = _parse_feed(r.text, feed, cutoff)
                posts.extend(items)

            except httpx.TimeoutException:
                errors.append(f"{feed['source']}: timeout")
            except Exception as e:
                errors.append(f"{feed['source']}: {str(e)[:80]}")

    return posts, errors


def _parse_feed(xml_text: str, feed: dict, cutoff: datetime.datetime) -> list[dict]:
    """Parse RSS 2.0 or Atom feed and return posts newer than cutoff."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Detect Atom vs RSS
    if root.tag.endswith("}feed") or root.tag == "feed":
        return _parse_atom(root, feed, cutoff)
    else:
        return _parse_rss(root, feed, cutoff)


def _parse_rss(root: ET.Element, feed: dict, cutoff: datetime.datetime) -> list[dict]:
    posts = []
    channel = root.find("channel")
    if channel is None:
        return posts

    for item in channel.findall("item"):
        title = item.findtext("title", "").strip()
        link  = item.findtext("link", "").strip()
        desc  = item.findtext("description", "").strip()
        guid  = item.findtext("guid", link).strip()
        pub   = item.findtext("pubDate", "").strip()

        created_at, ok = _parse_date(pub)
        if ok and created_at < cutoff:
            continue

        text = _clean_html(desc) or title
        post_id = guid.split("/")[-1] or guid

        if text and post_id:
            posts.append(_make_post(feed, post_id, title, text, link, created_at))

    return posts


def _parse_atom(root: ET.Element, feed: dict, cutoff: datetime.datetime) -> list[dict]:
    posts = []
    # Atom namespace
    ns = root.tag.split("}")[0].lstrip("{") if "}" in root.tag else ""
    def tag(name): return f"{{{ns}}}{name}" if ns else name

    for entry in root.findall(tag("entry")):
        title   = (entry.findtext(tag("title")) or "").strip()
        id_     = (entry.findtext(tag("id")) or "").strip()
        summary = (entry.findtext(tag("summary")) or "").strip()
        content = (entry.findtext(tag("content")) or "").strip()
        pub     = (entry.findtext(tag("published")) or entry.findtext(tag("updated")) or "").strip()

        link_el = entry.find(tag("link"))
        link = (link_el.get("href", "") if link_el is not None else "").strip()

        created_at, ok = _parse_date(pub)
        if ok and created_at < cutoff:
            continue

        text = _clean_html(content or summary) or title
        post_id = id_.split("/")[-1] or id_

        if text and post_id:
            posts.append(_make_post(feed, post_id, title, text, link, created_at))

    return posts


def _make_post(feed: dict, post_id: str, title: str, text: str, url: str, created_at) -> dict:
    return {
        "source":    feed["source"],
        "feed_name": feed["name"],
        "post_id":   post_id,
        "title":     title,
        "text":      text[:1000],   # cap length for CUGA
        "url":       url,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime.datetime) else "",
    }


def _parse_date(date_str: str) -> tuple:
    """Try RFC 2822 (RSS) and ISO 8601 (Atom). Returns (datetime, parsed_ok)."""
    if not date_str:
        return datetime.datetime.now(datetime.timezone.utc), False
    # RFC 2822
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt, True
    except Exception:
        pass
    # ISO 8601
    try:
        dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt, True
    except Exception:
        pass
    return datetime.datetime.now(datetime.timezone.utc), False


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode common entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text
        .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        .replace("\n", " ").replace("\r", "")
    )
    return re.sub(r"\s{2,}", " ", text).strip()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("examples.ai_feed_watcher.service:app", host="0.0.0.0", port=8002, reload=True)
