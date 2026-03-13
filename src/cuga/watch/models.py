"""
Pydantic models for the cuga watch configuration.

WatchConfig is the structured output of WatchInstructionParser.
It drives WatchExecutor, which wires everything into CugaWatcher.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class WatchSource(BaseModel):
    """Where to poll data from."""

    type: Literal["facebook_group", "web_page", "rss_feed", "custom"]
    """Source type — determines which source adapter is used."""

    url: str
    """URL of the resource to monitor."""

    name: str = ""
    """Human-readable label (defaults to URL if empty)."""

    interval_minutes: float = 30
    """How often to poll this source (minutes)."""

    extra: dict[str, Any] = Field(default_factory=dict)
    """Source-specific options (e.g. limit, css_selector)."""


class WatchCondition(BaseModel):
    """Filter that must match before the action is triggered."""

    type: Literal["keyword", "always", "custom"] = "keyword"
    """
    - keyword:  trigger when any keyword appears in the data
    - always:   trigger on every non-empty emission
    - custom:   use `expression` for a Python bool expression (advanced)
    """

    keywords: list[str] = Field(default_factory=list)
    """Keywords to match (case-insensitive). Used when type='keyword'."""

    expression: str = ""
    """Python expression string. Used when type='custom'."""


class WatchAction(BaseModel):
    """What to do when the condition is met."""

    type: Literal["email", "sms", "log", "agent_notify"]
    """
    - email:         send an email via SMTP
    - sms:           send an SMS via Twilio
    - log:           print/log to stdout
    - agent_notify:  delegate notification to CugaAgent (LLM-composed message)
    """

    # --- email fields ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_to: str = ""
    email_from: str = ""

    # --- sms fields ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    sms_from: str = ""
    sms_to: str = ""

    # --- shared / agent_notify ---
    extra: dict[str, Any] = Field(default_factory=dict)


class WatchConfig(BaseModel):
    """
    Complete watch specification parsed from a natural-language instruction.

    One WatchConfig drives one CugaWatcher run.
    """

    description: str = ""
    """Short human-readable summary of what is being watched."""

    sources: list[WatchSource] = Field(default_factory=list)
    condition: WatchCondition = Field(default_factory=WatchCondition)
    actions: list[WatchAction] = Field(default_factory=list)

    # Dispatch batching — all sources share one buffer; one action call per interval.
    # 0 = dispatch immediately per source (one email per matching source).
    # >0 = buffer matches across ALL sources and dispatch ONE consolidated newsletter
    #      every N minutes. Use this when you have multiple sources and want a single
    #      digest email rather than one email per source.
    dispatch_interval_minutes: float = 0

    # Archive behaviour (mirrors the Facebook monitor pattern)
    archive_enabled: bool = True
    archive_file: str = "watch_archive.jsonl"
    archive_interval_minutes: float = 2
