"""
WatchExecutor — converts a WatchConfig into a running CugaWatcher.

Architecture
------------
For each source in WatchConfig.sources, a @watcher.source task is registered.
For each action in WatchConfig.actions, a @watcher.on handler is registered.
An optional archive handler mirrors the facebook_monitor drain-buffer pattern.

CUGA's role
-----------
- If any action has type="agent_notify", CugaAgent is instantiated and used
  to compose the notification message (one LLM call per match).
- All other action types (email, sms, log) run as plain Python — no LLM involved.
- The LLM is therefore used at most twice in the entire lifecycle:
    1. Once at startup by WatchInstructionParser to parse the instruction.
    2. Once per keyword match by the agent_notify action (if configured).
"""

from __future__ import annotations

import asyncio
import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from loguru import logger

from cuga.watch.models import WatchAction, WatchConfig, WatchCondition, WatchSource
from cuga.watcher import CugaWatcher


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------

async def _fetch_facebook_group(source: WatchSource) -> list[dict]:
    """Scrape a Facebook group using a saved Playwright session."""
    session_file = source.extra.get("session_file", "./fb_session.json")
    limit = source.extra.get("limit", 20)

    if not Path(session_file).exists():
        logger.error(
            f"[watch] Facebook session file not found: {session_file}\n"
            "Run docs/examples/facebook_monitor/setup_session.py first."
        )
        return []

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=session_file)
            page = await context.new_page()
            try:
                await page.goto(source.url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(3000)
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(1500)
                posts = await page.evaluate("""() => {
                    const results = [];
                    const articles = document.querySelectorAll('[role="article"]');
                    for (const article of articles) {
                        const text = article.innerText?.trim();
                        if (!text || text.length <= 20) continue;

                        // Walk all <a> tags in the article looking for a post permalink.
                        // Priority: /permalink/ > /posts/ > story_fbid param > timestamp link.
                        let postUrl = null;
                        for (const a of article.querySelectorAll('a[href]')) {
                            const href = a.href;
                            if (!href || href.startsWith('javascript')) continue;
                            if (
                                href.includes('/permalink/') ||
                                (href.includes('/groups/') && href.includes('/posts/')) ||
                                href.includes('story_fbid=')
                            ) {
                                try {
                                    const u = new URL(href);
                                    // Strip click-tracking params so the link stays clean
                                    ['fbclid', '__cft__', '__tn__'].forEach(p => u.searchParams.delete(p));
                                    postUrl = u.origin + u.pathname + (u.search ? u.search : '');
                                } catch (_) {
                                    postUrl = href;
                                }
                                break;
                            }
                        }

                        results.push({ text: text.substring(0, 1000), post_url: postUrl });
                    }
                    return results;
                }""")
                return [
                    {
                        "text": p["text"],
                        "url": p["post_url"] or source.url,
                        "post_url": p["post_url"],        # direct link to the post (None if not found)
                        "source_name": source.name,
                    }
                    for p in posts[:limit]
                ]
            finally:
                await context.close()
                await browser.close()
    except ImportError:
        logger.error("[watch] Playwright not installed. Run: pip install playwright && playwright install chromium")
        return []
    except Exception as e:
        logger.exception(f"[watch] Error scraping {source.url}: {e}")
        return []


async def _fetch_web_page(source: WatchSource) -> list[dict]:
    """Fetch a web page or RSS feed and return items as dicts."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(source.url, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text

        # Try RSS/Atom first
        if "<rss" in text or "<feed" in text or "<atom" in text:
            return _parse_rss(text, source)

        # Plain HTML — return as a single item
        return [{"text": text[:2000], "url": source.url, "source_name": source.name}]
    except Exception as e:
        logger.exception(f"[watch] Error fetching {source.url}: {e}")
        return []


def _parse_rss(xml_text: str, source: WatchSource) -> list[dict]:
    import xml.etree.ElementTree as ET
    items = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        # RSS 2.0
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            desc = item.findtext("description", "")
            link = item.findtext("link", source.url)
            items.append({"text": f"{title}\n{desc}".strip(), "url": link, "source_name": source.name})
        # Atom
        if not items:
            for entry in root.findall(".//atom:entry", ns):
                title = entry.findtext("atom:title", "", ns)
                summary = entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns)
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", source.url) if link_el is not None else source.url
                items.append({"text": f"{title}\n{summary}".strip(), "url": link, "source_name": source.name})
    except Exception as e:
        logger.warning(f"[watch] RSS parse error: {e}")
    return items[:source.extra.get("limit", 50)]


async def _fetch_source(source: WatchSource) -> list[dict]:
    if source.type == "facebook_group":
        return await _fetch_facebook_group(source)
    return await _fetch_web_page(source)


# ---------------------------------------------------------------------------
# Condition helpers
# ---------------------------------------------------------------------------

def _matches(items: list[dict], condition: WatchCondition) -> bool:
    if condition.type == "always":
        return bool(items)
    if condition.type == "keyword":
        kws = [k.lower() for k in condition.keywords]
        return any(
            any(kw in item.get("text", "").lower() for kw in kws)
            for item in items
        )
    if condition.type == "custom" and condition.expression:
        try:
            return bool(eval(condition.expression, {"items": items}))  # noqa: S307
        except Exception as e:
            logger.warning(f"[watch] Custom expression error: {e}")
    return False


def _filter_matches(items: list[dict], condition: WatchCondition) -> list[dict]:
    if condition.type == "always":
        return items
    if condition.type == "keyword":
        kws = [k.lower() for k in condition.keywords]
        result = []
        for item in items:
            matched = [kw for kw in kws if kw in item.get("text", "").lower()]
            if matched:
                result.append({**item, "matched_keywords": matched})
        return result
    return items


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _send_email(action: WatchAction, subject: str, body: str) -> None:
    username = action.smtp_username or os.environ.get("WATCH_EMAIL_USERNAME", "")
    password = action.smtp_password or os.environ.get("WATCH_EMAIL_PASSWORD", "")
    from_addr = action.email_from or username
    to_addr = action.email_to

    if not username or not password:
        logger.warning("[watch] Email credentials not set — skipping email. Set smtp_username/smtp_password in action or WATCH_EMAIL_USERNAME/WATCH_EMAIL_PASSWORD env vars.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(action.smtp_host, action.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(username, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        logger.info(f"[watch] Email sent to {to_addr}: {subject}")
    except Exception as e:
        logger.error(f"[watch] Email failed: {e}")


def _send_sms(action: WatchAction, message: str) -> None:
    account_sid = action.twilio_account_sid or os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = action.twilio_auth_token or os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not account_sid or not auth_token:
        logger.warning("[watch] Twilio credentials not set — skipping SMS.")
        return
    try:
        from twilio.rest import Client  # type: ignore
        client = Client(account_sid, auth_token)
        sms = client.messages.create(body=message, from_=action.sms_from, to=action.sms_to)
        logger.info(f"[watch] SMS sent (SID: {sms.sid})")
    except ImportError:
        logger.error("[watch] Twilio not installed. Run: pip install twilio")
    except Exception as e:
        logger.error(f"[watch] SMS failed: {e}")


async def _dispatch_action(
    action: WatchAction,
    matches: list[dict],
    condition: WatchCondition,
    cuga_agent: Any,
    thread_id: str = "",
) -> None:
    if not matches:
        return

    # Build a compact summary
    keywords_found = sorted(set(kw for m in matches for kw in m.get("matched_keywords", [])))
    sources_found = sorted(set(m.get("source_name", m.get("url", "")) for m in matches))
    subject = f"Watch alert: {', '.join(keywords_found) or 'match'} in {', '.join(sources_found)}"
    body_lines = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {len(matches)} match(es) found\n"]
    for m in matches:
        post_url = m.get("post_url") or m.get("url", "")
        body_lines.append(
            f"Source: {m.get('source_name', m.get('url', ''))}\n"
            f"Keywords: {', '.join(m.get('matched_keywords', []))}\n"
            f"Link: {post_url}\n"
            f"Excerpt: {m.get('text', '')[:300]}\n"
        )
    body = "\n---\n".join(body_lines)

    if action.type == "email":
        _send_email(action, subject, body)

    elif action.type == "sms":
        sms_text = f"{subject}\n{matches[0].get('text', '')[:100]}"
        _send_sms(action, sms_text)

    elif action.type == "log":
        logger.info(f"[watch] MATCH: {subject}\n{body}")

    elif action.type == "agent_notify":
        if cuga_agent is None:
            logger.warning("[watch] agent_notify requested but no CugaAgent available — falling back to log")
            logger.info(f"[watch] MATCH: {subject}\n{body}")
            return
        task = (
            f"Keyword match(es) found. Send a consolidated notification.\n\n"
            f"MATCHES:\n{body}\n\n"
            f"Compose a clear summary and send it (email if tools available, otherwise log it)."
        )
        try:
            # Level 2: stable thread_id lets the agent accumulate memory across events
            result = await cuga_agent.invoke(task, thread_id=thread_id or None)
            logger.info(f"[watch] CugaAgent response (thread={thread_id or 'default'}): {result.answer}")
        except Exception as e:
            logger.error(f"[watch] CugaAgent notification failed: {e}")


# ---------------------------------------------------------------------------
# WatchExecutor
# ---------------------------------------------------------------------------


class WatchExecutor:
    """
    Wires a WatchConfig into CugaWatcher and starts the event loop.

    Parameters
    ----------
    config : WatchConfig
        Parsed watch specification.
    cuga_agent : optional
        A CugaAgent instance.  Required only if any action has type="agent_notify".
        If not provided, a CugaAgent is auto-created for agent_notify actions.
    thread_id : str, optional
        Stable conversation thread ID passed to CugaAgent.invoke() for
        agent_notify actions.  Using the same thread_id across multiple match
        events gives the agent persistent memory — it can reason across events
        (e.g. "this is the 3rd nanny post this week").
        Defaults to "" which lets each invoke start a fresh context.
    """

    def __init__(self, config: WatchConfig, cuga_agent: Any = None, thread_id: str = "") -> None:
        self.config = config
        self._cuga_agent = cuga_agent
        self._thread_id = thread_id

        # Auto-create CugaAgent if agent_notify is requested
        if any(a.type == "agent_notify" for a in config.actions) and cuga_agent is None:
            try:
                from cuga.sdk import CugaAgent
                self._cuga_agent = CugaAgent(tools=[])
                logger.info("[watch] Auto-created CugaAgent for agent_notify actions")
            except Exception as e:
                logger.warning(f"[watch] Could not create CugaAgent: {e}")

        self._watcher = CugaWatcher(self._cuga_agent) if self._cuga_agent else _NullAgentWatcher()

    async def run(self) -> None:
        """Build sources + handlers and start the watcher (runs forever)."""
        config = self.config

        # --- Shared in-memory archive buffer ---
        archive_buffer: list[dict] = []
        archive_lock = asyncio.Lock()

        # --- Register one source per WatchSource ---
        source_fns = []
        for src in config.sources:
            # Capture src in closure
            async def _source_fn(src=src) -> list[dict]:
                items = await _fetch_source(src)
                if items and config.archive_enabled:
                    async with archive_lock:
                        archive_buffer.extend(items)
                return items

            _source_fn.__name__ = f"watch_{src.name or src.type}"
            self._watcher.source(
                every_minutes=src.interval_minutes,
                name=_source_fn.__name__,
            )(_source_fn)
            source_fns.append(_source_fn)

        # --- Condition predicate ---
        cond = config.condition

        def _predicate(items: list[dict]) -> bool:
            return _matches(items, cond)

        # --- Register one handler per source that dispatches all actions ---
        actions = config.actions
        agent = self._cuga_agent
        thread_id = self._thread_id

        for src_fn in source_fns:
            async def _notify_handler(
                items: list[dict],
                cond=cond,
                actions=actions,
                agent=agent,
                thread_id=thread_id,
            ):
                matches = _filter_matches(items, cond)
                await asyncio.gather(*(
                    _dispatch_action(action, matches, cond, agent, thread_id=thread_id)
                    for action in actions
                ))

            _notify_handler.__name__ = f"notify_{src_fn.__name__}"
            self._watcher.on(src_fn, when=_predicate)(_notify_handler)

        # --- Archive drain source (every 2 min) ---
        if config.archive_enabled:
            archive_path = Path(config.archive_file)

            async def _drain_buffer() -> list[dict]:
                async with archive_lock:
                    if not archive_buffer:
                        return []
                    batch = list(archive_buffer)
                    archive_buffer.clear()
                return batch

            self._watcher.source(
                every_minutes=config.archive_interval_minutes,
                name="archive_drain",
                run_immediately=False,
            )(_drain_buffer)

            async def _serialize(items: list[dict]) -> None:
                with archive_path.open("a", encoding="utf-8") as f:
                    for item in items:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
                logger.info(f"[watch] Archived {len(items)} item(s) → {archive_path.name}")

            self._watcher.on(_drain_buffer)(_serialize)

        # --- Print startup summary ---
        kws = config.condition.keywords
        action_types = [a.type for a in config.actions]
        logger.info(
            f"[cuga watch] Starting — {config.description!r}\n"
            f"  Sources:    {[s.name or s.url for s in config.sources]}\n"
            f"  Condition:  {config.condition.type}"
            + (f" → {kws}" if kws else "")
            + f"\n  Actions:    {action_types}\n"
            f"  Archive:    {'every ' + str(config.archive_interval_minutes) + ' min → ' + config.archive_file if config.archive_enabled else 'disabled'}"
        )

        await self._watcher.start()


class _NullAgentWatcher(CugaWatcher):
    """CugaWatcher without a real CugaAgent (for log/email/sms-only setups)."""

    def __init__(self) -> None:  # type: ignore[override]
        self.agent = None
        self._sources: list = []
        self._handlers: list = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._tasks: list = []
