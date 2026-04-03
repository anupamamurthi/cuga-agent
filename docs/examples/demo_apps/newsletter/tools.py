"""
Newsletter tools — LangChain @tool definitions.

  fetch_rss   : fetch and keyword-filter an RSS/Atom feed, returns JSON
  send_email  : send an HTML newsletter via SMTP

SMTP credentials are read from environment variables:
  SMTP_HOST       (default: smtp.gmail.com)
  SMTP_PORT       (default: 587)
  SMTP_USERNAME   your Gmail / SMTP address
  SMTP_PASSWORD   app password or SMTP password
  NEWSLETTER_TO   comma-separated recipient addresses
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def fetch_rss(
    url: str,
    keywords: Optional[list[str]] = None,
    max_items: int = 30,
) -> str:
    """
    Fetch an RSS or Atom feed and return matching items as a JSON string.

    Args:
        url:       The RSS/Atom feed URL to fetch.
        keywords:  Optional list of keywords to filter by (case-insensitive,
                   matched against title + summary). Pass None to return all items.
        max_items: Maximum number of items to return (default 30).

    Returns:
        JSON array of objects with keys: title, url, summary, source, published.
        Returns an error string if the feed cannot be fetched.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "cuga-newsletter/1.0 (RSS reader)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as exc:
        return json.dumps({"error": f"Failed to fetch {url}: {exc}"})

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return json.dumps({"error": f"Failed to parse XML from {url}: {exc}"})

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict] = []

    channel = root.find("channel")
    if channel is not None:
        source_name = (channel.findtext("title") or url).strip()
        for entry in channel.findall("item"):
            title   = (entry.findtext("title") or "").strip()
            link    = (entry.findtext("link") or "").strip()
            summary = (entry.findtext("description") or "").strip()
            published = (entry.findtext("pubDate") or "").strip()
            items.append({"title": title, "url": link,
                          "summary": _truncate(summary, 300),
                          "source": source_name, "published": published})

    elif root.tag in ("{http://www.w3.org/2005/Atom}feed", "feed"):
        source_name = (root.findtext("{http://www.w3.org/2005/Atom}title") or url).strip()
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            title    = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el  = entry.find("{http://www.w3.org/2005/Atom}link")
            link     = (link_el.get("href") or "") if link_el is not None else ""
            summ_el  = (entry.find("{http://www.w3.org/2005/Atom}summary")
                        or entry.find("{http://www.w3.org/2005/Atom}content"))
            summary  = (summ_el.text or "").strip() if summ_el is not None else ""
            published = (entry.findtext("{http://www.w3.org/2005/Atom}updated")
                         or entry.findtext("{http://www.w3.org/2005/Atom}published") or "")
            items.append({"title": title, "url": link,
                          "summary": _truncate(summary, 300),
                          "source": source_name, "published": published})

    if keywords:
        kw_lower = [k.lower() for k in keywords]
        items = [
            it for it in items
            if any(kw in it["title"].lower() or kw in it["summary"].lower() for kw in kw_lower)
        ]

    items = items[:max_items]
    logger.info("fetch_rss: %s → %d items (after filter)", url, len(items))
    return json.dumps(items, ensure_ascii=False)


@tool
def send_email(subject: str, html_body: str, to: Optional[str] = None) -> str:
    """
    Send an HTML newsletter email via SMTP.

    Reads connection details from environment variables:
      SMTP_HOST       SMTP server hostname (default: smtp.gmail.com)
      SMTP_PORT       SMTP port (default: 587, uses STARTTLS)
      SMTP_USERNAME   Sender email address
      SMTP_PASSWORD   SMTP / app password
      NEWSLETTER_TO   Comma-separated recipient addresses (fallback if `to` not provided)

    Args:
        subject:   Email subject line.
        html_body: Full HTML content of the newsletter.
        to:        Recipient address(es), comma-separated. Falls back to NEWSLETTER_TO env var.

    Returns:
        "sent to <recipients>" on success, or an error message string.
    """
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    username  = os.environ.get("SMTP_USERNAME", "")
    password  = os.environ.get("SMTP_PASSWORD", "")
    to_raw    = to or os.environ.get("NEWSLETTER_TO", "")

    if not username or not password:
        return "Error: SMTP_USERNAME and SMTP_PASSWORD environment variables are required."
    if not to_raw:
        return "Error: NEWSLETTER_TO environment variable is required."

    recipients = [a.strip() for a in to_raw.split(",") if a.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = username
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(username, password)
            server.sendmail(username, recipients, msg.as_string())
        logger.info("send_email: sent '%s' to %s", subject, recipients)
        return f"sent to {recipients}"
    except Exception as exc:
        logger.error("send_email failed: %s", exc)
        return f"Error sending email: {exc}"


def _truncate(text: str, max_len: int) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text[:max_len] + "…" if len(text) > max_len else text
