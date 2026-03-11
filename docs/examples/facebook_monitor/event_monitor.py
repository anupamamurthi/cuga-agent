"""
Facebook Group Monitor — powered by CugaWatcher
================================================
Uses CugaWatcher (from the CUGA SDK) to wire two sources to their handlers:

  Source A: fetch_posts   (every 30 min — configurable via check_interval_minutes)
    → handler: notify     — invokes CUGA to send email/SMS when keywords are found

  Source B: drain_buffer  (every 2 min)
    → handler: serialize  — flushes fetched posts to posts_archive.jsonl

Source A populates an in-memory buffer; Source B drains and serializes it.
Facebook is only scraped every 30 minutes; the archive is flushed every 2 minutes.

Usage:
    python event_monitor.py
"""

import os
import asyncio
import json
import yaml
from datetime import datetime
from pathlib import Path

from loguru import logger

os.environ["ENV_FILE"] = str(Path(__file__).parents[3] / ".env")
os.environ["DYNA_CONF_ADVANCED_FEATURES__MODE"] = "api"
os.environ["DYNA_CONF_FEATURES__LOCAL_SANDBOX"] = "true"

from cuga import CugaAgent, CugaWatcher

import fb_tools
from fb_tools import tools as fb_tool_list
from state import SeenPostsStore, make_post_id

ARCHIVE_FILE = Path(__file__).parent / "posts_archive.jsonl"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "./config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Shared in-memory buffer: Source A fills it, Source B drains it
# ---------------------------------------------------------------------------
# Each entry: {"post_id", "group_name", "group_url", "text", "fetched_at"}

_post_buffer: list[dict] = []
_buffer_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Source A — scrape Facebook every N minutes, deduplicate, fill buffer
# ---------------------------------------------------------------------------

async def fetch_posts_source(config: dict, seen: SeenPostsStore) -> list[dict]:
    groups = config["groups"]
    limit = config.get("posts_per_group", 20)
    session_file = config.get("facebook_session_file", "./fb_session.json")

    all_new: list[dict] = []
    for group in groups:
        try:
            raw = await fb_tools._scrape_group_async(group["url"], session_file, limit)
            new_count = 0
            for post in raw:
                text = post.get("text", "").strip()
                if not text:
                    continue
                post_id = make_post_id(group["url"], text)
                if seen.is_new(post_id):
                    seen.mark_seen(post_id)
                    record = {
                        "post_id": post_id,
                        "group_name": group["name"],
                        "group_url": group["url"],
                        "text": text,
                        "fetched_at": datetime.now().isoformat(),
                    }
                    all_new.append(record)
                    new_count += 1
            logger.info(
                f"[fetch_posts] {group['name']}: {new_count} new / "
                f"{len(raw) - new_count} already seen"
            )
        except Exception:
            logger.exception(f"[fetch_posts] Error scraping {group['name']}")

    if all_new:
        async with _buffer_lock:
            _post_buffer.extend(all_new)
        logger.info(f"[fetch_posts] {len(all_new)} new post(s) buffered for archiving")

    return all_new  # dispatched to the notify handler


# ---------------------------------------------------------------------------
# Source B — drain the in-memory buffer every 2 minutes
# ---------------------------------------------------------------------------

async def drain_post_buffer() -> list[dict]:
    async with _buffer_lock:
        if not _post_buffer:
            return []
        batch = list(_post_buffer)
        _post_buffer.clear()
    return batch  # dispatched to the serialize handler


# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------

def _get_matches(posts: list[dict], keywords: list[str]) -> list[dict]:
    kw_lower = [k.lower() for k in keywords]
    results = []
    for post in posts:
        matched = [kw for kw in kw_lower if kw in post["text"].lower()]
        if matched:
            results.append({**post, "matched_keywords": matched})
    return results


def _has_keyword_match(posts: list[dict], keywords: list[str]) -> bool:
    return bool(_get_matches(posts, keywords))


# ---------------------------------------------------------------------------
# Handler A — keyword match → invoke CUGA to send notification
# ---------------------------------------------------------------------------

def _build_notify_task(matches: list[dict], config: dict) -> str:
    email_enabled = config["notifications"]["email"].get("enabled", False)
    sms_enabled = config["notifications"]["sms"].get("enabled", False)
    notify_via = []
    if email_enabled:
        notify_via.append("email using send_email_alert")
    if sms_enabled:
        notify_via.append("SMS using send_sms_alert")
    if not notify_via:
        notify_via = ["email using send_email_alert"]

    matches_text = "\n\n".join(
        f"Group: {m['group_name']}\n"
        f"Keywords matched: {', '.join(m['matched_keywords'])}\n"
        f"Post excerpt: {m['text'][:300]}"
        for m in matches
    )
    all_keywords = sorted(set(kw for m in matches for kw in m["matched_keywords"]))
    all_groups = sorted(set(m["group_name"] for m in matches))

    return f"""
You are a notification agent. Keyword matches were found in monitored Facebook groups.
Send a single consolidated notification to the user.

MATCHES ({len(matches)} total):
{matches_text}

INSTRUCTIONS:
1. Compose a clear notification:
   - Subject: "Facebook alert: [{', '.join(all_keywords)}] found in [{', '.join(all_groups)}]"
   - Body: list each match with group name, matched keyword(s), and a short excerpt
2. Send via: {' and '.join(notify_via)}
3. Return a confirmation of what was sent.

Detected at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
""".strip()


async def keyword_match_handler(posts: list[dict], agent: CugaAgent, config: dict) -> None:
    matches = _get_matches(posts, config["keywords"])
    if not matches:
        return
    logger.info(f"[notify] {len(matches)} keyword match(es) — invoking CUGA")
    task = _build_notify_task(matches, config)
    try:
        result = await agent.invoke(task)
        logger.info(f"[notify] CUGA response: {result.answer}")
    except Exception:
        logger.exception("[notify] CUGA invocation failed")


# ---------------------------------------------------------------------------
# Handler B — serialize posts to JSONL archive
# ---------------------------------------------------------------------------

async def serialize_handler(posts: list[dict]) -> None:
    with ARCHIVE_FILE.open("a", encoding="utf-8") as f:
        for post in posts:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")
    logger.info(f"[serialize] Archived {len(posts)} post(s) → {ARCHIVE_FILE.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    config = load_config()
    fb_tools.set_config(config)
    seen = SeenPostsStore()

    agent = CugaAgent(tools=fb_tool_list)
    for tool in fb_tool_list:
        tool.metadata = {"server_name": "facebook_monitor"}

    watcher = CugaWatcher(agent)

    fetch_interval = config.get("check_interval_minutes", 30)

    # Source A: scrape Facebook every N minutes
    @watcher.source(every_minutes=fetch_interval, name="fetch_posts")
    async def fetch_posts():
        return await fetch_posts_source(config, seen)

    # Source B: drain in-memory buffer every 2 minutes
    @watcher.source(every_minutes=2, name="drain_buffer", run_immediately=False)
    async def drain_buffer():
        return await drain_post_buffer()

    # Handler: keyword match on fetched posts → CUGA notification
    @watcher.on(fetch_posts, when=lambda posts: _has_keyword_match(posts, config["keywords"]))
    async def notify(posts):
        await keyword_match_handler(posts, agent, config)

    # Handler: always serialize whatever the buffer drain yields
    @watcher.on(drain_buffer)  # no `when` = runs whenever drain_buffer returns non-empty
    async def serialize(posts):
        await serialize_handler(posts)

    logger.info(
        f"Facebook Monitor (CugaWatcher) started.\n"
        f"  Groups:          {[g['name'] for g in config['groups']]}\n"
        f"  Keywords:        {config['keywords']}\n"
        f"  Fetch interval:  every {fetch_interval} min\n"
        f"  Serialize every: 2 min  →  {ARCHIVE_FILE.name}\n"
        f"  Seen posts:      {len(seen)} (from previous runs)"
    )

    await watcher.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped.")
