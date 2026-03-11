"""
WatchInstructionParser — converts a natural-language watch instruction into
a WatchConfig using a single structured LLM call.

The LLM is only used here, once, at startup.  It is NOT used in the
monitoring loop — that runs purely in Python via CugaWatcher.
"""

from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger

from cuga.watch.models import WatchAction, WatchCondition, WatchConfig, WatchSource

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are a configuration parser for an event-monitoring system called "cuga watch".
Given a natural-language monitoring instruction, extract a structured WatchConfig JSON object.

Rules:
- sources[].type: one of "facebook_group", "web_page", "rss_feed", "custom"
- sources[].url: the URL to monitor (ask the user if completely missing — use placeholder "REQUIRED")
- sources[].interval_minutes: how often to poll (default 30)
- condition.type: "keyword" if specific words are mentioned, "always" otherwise
- condition.keywords: list of keywords (lower-case), empty if condition.type != "keyword"
- actions[].type: "email" if email address given, "sms" if phone number given, "log" otherwise
- actions[].email_to: recipient email address (empty string if not provided)
- actions[].sms_to: recipient phone number (empty string if not provided)
- For email: smtp_host="smtp.gmail.com", smtp_port=587; leave username/password empty (user fills in .env or config)
- archive_enabled: true by default
- archive_file: "watch_archive.jsonl"
- archive_interval_minutes: 2

Output ONLY valid JSON conforming to the WatchConfig schema. No markdown, no explanation.
""".strip()

_USER_PROMPT_TEMPLATE = """
Instruction: {instruction}

Respond with a single JSON object.
""".strip()

# ---------------------------------------------------------------------------
# Example schema stub (shown to the LLM for reference)
# ---------------------------------------------------------------------------

_SCHEMA_HINT = {
    "description": "short description",
    "sources": [
        {
            "type": "facebook_group",
            "url": "https://www.facebook.com/groups/123",
            "name": "My Group",
            "interval_minutes": 30,
            "extra": {},
        }
    ],
    "condition": {"type": "keyword", "keywords": ["nanny", "sitter"], "expression": ""},
    "actions": [
        {
            "type": "email",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_username": "",
            "smtp_password": "",
            "email_to": "you@example.com",
            "email_from": "",
            "twilio_account_sid": "",
            "twilio_auth_token": "",
            "sms_from": "",
            "sms_to": "",
            "extra": {},
        }
    ],
    "archive_enabled": True,
    "archive_file": "watch_archive.jsonl",
    "archive_interval_minutes": 2,
}


class WatchInstructionParser:
    """
    Parses a natural-language watch instruction into a WatchConfig.

    Uses whichever LLM is configured via the CUGA settings (WatsonX, OpenAI, etc.).
    Falls back to a heuristic parser if the LLM call fails.
    """

    def __init__(self) -> None:
        self._llm: Any = None

    def _get_llm(self) -> Any:
        if self._llm is not None:
            return self._llm
        try:
            from cuga.config import settings
            from cuga.backend.llm.models import LLMManager

            llm_manager = LLMManager()
            # Pass the DynaBox directly — LLMManager._create_cache_key calls .to_dict()
            model_settings = settings.agent.task_decomposition.model
            self._llm = llm_manager.get_model(model_settings)
        except Exception as e:
            logger.warning(f"[WatchParser] Could not load CUGA LLM ({e}) — using fallback heuristic parser")
            self._llm = None
        return self._llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, instruction: str) -> WatchConfig:
        """
        Parse a natural-language instruction into a WatchConfig.

        Tries the LLM first; falls back to the heuristic parser on error.
        """
        llm = self._get_llm()
        if llm is not None:
            try:
                return self._parse_with_llm(instruction, llm)
            except Exception as e:
                logger.warning(f"[WatchParser] LLM parse failed ({e}), using heuristic fallback")
        return self._parse_heuristic(instruction)

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------

    def _parse_with_llm(self, instruction: str, llm: Any) -> WatchConfig:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=f"{_SYSTEM_PROMPT}\n\nSchema example:\n{json.dumps(_SCHEMA_HINT, indent=2)}"),
            HumanMessage(content=_USER_PROMPT_TEMPLATE.format(instruction=instruction)),
        ]

        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        raw = json.loads(content)
        config = WatchConfig(**raw)
        logger.info(f"[WatchParser] LLM parsed config: {config.description!r}")
        return config

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _parse_heuristic(self, instruction: str) -> WatchConfig:
        """
        Best-effort extraction without an LLM.
        Looks for URLs, email addresses, phone numbers, and keywords.
        """
        import re

        text = instruction.lower()

        # Extract URLs
        urls = re.findall(r"https?://[^\s,\"']+", instruction)

        # Determine source type
        sources = []
        for url in urls:
            src_type = "web_page"
            if "facebook.com/groups" in url:
                src_type = "facebook_group"
            elif url.endswith(".rss") or "feed" in url:
                src_type = "rss_feed"
            sources.append(
                WatchSource(
                    type=src_type,
                    url=url,
                    name=url,
                    interval_minutes=30,
                )
            )

        if not sources:
            sources = [WatchSource(type="web_page", url="REQUIRED", name="unknown")]

        # Extract keywords (words after "keyword", "looking for", "mention", etc.)
        kw_pattern = re.compile(
            r"(?:keyword[s]?|mention[s]?|looking for|alert on|watch for)[:\s]+(['\"]?[\w\s,]+['\"]?)",
            re.IGNORECASE,
        )
        keywords: list[str] = []
        for match in kw_pattern.finditer(instruction):
            raw_kws = match.group(1).strip("'\", ")
            keywords.extend(k.strip().lower() for k in re.split(r"[,\s]+", raw_kws) if k.strip())

        condition = WatchCondition(
            type="keyword" if keywords else "always",
            keywords=keywords,
        )

        # Extract email address
        emails = re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", instruction)

        # Extract phone numbers (very rough)
        phones = re.findall(r"\+?\d[\d\s\-().]{7,}\d", instruction)

        actions: list[WatchAction] = []
        for email in emails:
            actions.append(WatchAction(type="email", email_to=email))
        for phone in phones:
            actions.append(WatchAction(type="sms", sms_to=phone.replace(" ", "")))
        if not actions:
            actions.append(WatchAction(type="log"))

        config = WatchConfig(
            description=instruction[:120],
            sources=sources,
            condition=condition,
            actions=actions,
        )
        logger.info(f"[WatchParser] Heuristic parsed config: {config.description!r}")
        return config
