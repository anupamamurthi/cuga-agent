"""
Universal channel and tool registry.

Every channel type and agent tool available in cuga++ is declared here with:
  - human-readable description (shown to the planner LLM)
  - constructor parameters with types, defaults, and descriptions
  - required environment variables (shown in error messages)

The planner agent reads this registry to decide which channels and tools
to wire for each user request. Nothing in this file starts any process —
it's a static manifest, not a factory.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChannelSpec:
    type_name:   str
    role:        str          # "data" | "trigger" | "output"
    description: str
    params:      dict[str, tuple]   # {name: (type, default, description)}
    env_vars:    list[str] = field(default_factory=list)


@dataclass
class ToolSpec:
    name:        str
    description: str
    env_vars:    list[str] = field(default_factory=list)  # required — warn if missing


# ---------------------------------------------------------------------------
# Data Channels — continuously collect data into the pipeline buffer
# ---------------------------------------------------------------------------

CHANNEL_REGISTRY: dict[str, ChannelSpec] = {

    "rss": ChannelSpec(
        type_name="rss",
        role="data",
        description=(
            "Poll RSS/Atom feeds for new articles. Good for: news digests, research monitoring, "
            "blog aggregation, arxiv/HackerNews/VentureBeat feeds."
        ),
        params={
            "rss_sources":   (list, None,  "List of RSS/Atom feed URLs to monitor"),
            "rss_keywords":  (list, None,  "Filter keywords — only items matching these pass through. Omit to accept all."),
            "poll_minutes":  (float, 15,   "How often to poll feeds in minutes"),
        },
    ),

    "imap": ChannelSpec(
        type_name="imap",
        role="data",
        description=(
            "Watch an email inbox for new unread messages. Good for: support ticket triage, "
            "email digests, monitoring a shared inbox."
        ),
        params={
            "imap_host":     (str, "",       "IMAP server hostname (e.g. imap.gmail.com)"),
            "imap_username": (str, "",       "IMAP username / email address"),
            "imap_folder":   (str, "INBOX",  "Mailbox folder to watch"),
            "poll_minutes":  (float, 5,      "How often to check for new mail"),
        },
        env_vars=["IMAP_PASSWORD"],
    ),

    "slack_data": ChannelSpec(
        type_name="slack_data",
        role="data",
        description=(
            "Poll a Slack channel for new messages. Good for: summarising team discussions, "
            "monitoring a support or alert channel."
        ),
        params={
            "slack_channel":  (str, "",  "Slack channel name or ID (e.g. #general or C01234)"),
            "poll_minutes":   (float, 2, "How often to poll for new messages"),
        },
        env_vars=["SLACK_BOT_TOKEN"],
    ),

    "telegram_data": ChannelSpec(
        type_name="telegram_data",
        role="data",
        description=(
            "Receive messages sent to a Telegram bot (long-polling). Good for: building a "
            "Telegram assistant, relaying messages to another channel."
        ),
        params={
            "allowed_user_ids": (list, None, "Whitelist of Telegram user IDs. Omit to allow all."),
        },
        env_vars=["TELEGRAM_BOT_TOKEN"],
    ),

    "discord_data": ChannelSpec(
        type_name="discord_data",
        role="data",
        description=(
            "Poll a Discord channel for new messages. Good for: community digest, "
            "moderating or summarising a Discord server."
        ),
        params={
            "discord_channel_id": (str, "",   "Discord channel ID"),
            "poll_seconds":       (float, 10, "How often to poll in seconds"),
        },
        env_vars=["DISCORD_BOT_TOKEN"],
    ),

    "docling": ChannelSpec(
        type_name="docling",
        role="data",
        description=(
            "Watch a folder for new documents (PDF, images, Word, PowerPoint) and extract "
            "their text with docling before the agent fires. Good for: document processing "
            "pipelines, contract review, report summarisation."
        ),
        params={
            "watch_folder": (str, "./watched",   "Folder path to watch for new files"),
            "poll_minutes": (float, 1,           "How often to check for new files"),
        },
    ),

    "audio": ChannelSpec(
        type_name="audio",
        role="data",
        description=(
            "Watch a folder for audio files and transcribe them with Whisper. Good for: "
            "meeting notes, voice memos, podcast summaries."
        ),
        params={
            "watch_folder":   (str, "./recordings", "Folder path to watch for audio files"),
            "whisper_model":  (str, "base",         "Whisper model: tiny | base | small | medium | large"),
        },
    ),

    # -----------------------------------------------------------------------
    # Output Channels
    # -----------------------------------------------------------------------

    "email": ChannelSpec(
        type_name="email",
        role="output",
        description="Send HTML output via SMTP email.",
        params={
            "output_target":  (str, "",              "Recipient email address"),
            "output_subject": (str, "CUGA Digest",   "Email subject prefix"),
        },
        env_vars=["SMTP_USERNAME", "SMTP_PASSWORD"],
    ),

    "slack": ChannelSpec(
        type_name="slack",
        role="output",
        description="Post output to a Slack channel (webhook URL or bot token).",
        params={
            "output_target": (str, "", "Slack webhook URL, or channel name/ID if using SLACK_BOT_TOKEN"),
        },
        env_vars=["SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN"],
    ),

    "telegram": ChannelSpec(
        type_name="telegram",
        role="output",
        description="Send output as a Telegram message.",
        params={
            "output_target": (str, "", "Telegram chat ID or @username"),
        },
        env_vars=["TELEGRAM_BOT_TOKEN"],
    ),

    "discord": ChannelSpec(
        type_name="discord",
        role="output",
        description="Post output to a Discord channel (webhook URL or bot token).",
        params={
            "output_target": (str, "", "Discord webhook URL, or channel ID if using DISCORD_BOT_TOKEN"),
        },
        env_vars=["DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN"],
    ),

    "sms": ChannelSpec(
        type_name="sms",
        role="output",
        description="Send output as an SMS via Twilio.",
        params={
            "output_target": (str, "", "Recipient phone number in E.164 format (e.g. +15551234567)"),
        },
        env_vars=["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"],
    ),

    "log": ChannelSpec(
        type_name="log",
        role="output",
        description="Print output to the console. No credentials required — use this for testing.",
        params={},
    ),
}

# ---------------------------------------------------------------------------
# Tool Registry — on-demand capabilities given to pipeline agents
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, ToolSpec] = {

    "web_search": ToolSpec(
        name="web_search",
        description=(
            "Search the web in real time (Tavily). Use when the pipeline needs to fetch "
            "current news, look up facts, or monitor topics that aren't in a feed."
        ),
        env_vars=["TAVILY_API_KEY"],
    ),

    "calendar": ToolSpec(
        name="calendar",
        description=(
            "Read and create Google Calendar events. Use for daily briefings, "
            "meeting summaries, or scheduling reminders."
        ),
        env_vars=["GOOGLE_CALENDAR_ACCESS_TOKEN"],
    ),

    "github": ToolSpec(
        name="github",
        description=(
            "Read GitHub pull requests, issues, and CI/CD workflow runs. Use for "
            "dev team digests, PR review reminders, build failure alerts."
        ),
        env_vars=["GITHUB_TOKEN"],
    ),

    "market_data": ToolSpec(
        name="market_data",
        description=(
            "Get cryptocurrency prices (CoinGecko, free) and stock quotes (Alpha Vantage). "
            "Use for price alerts, portfolio summaries, financial digests."
        ),
        env_vars=["ALPHA_VANTAGE_API_KEY"],   # only needed for stocks; crypto is free
    ),

    "image_gen": ToolSpec(
        name="image_gen",
        description=(
            "Generate images with DALL-E. Use when the pipeline output should include images."
        ),
        env_vars=["OPENAI_API_KEY"],
    ),

    "rag": ToolSpec(
        name="rag",
        description=(
            "Ingest and search documents in a local ChromaDB vector database. Use for "
            "knowledge-base Q&A pipelines, document recall, semantic search over past content."
        ),
        env_vars=[],
    ),

    "shell": ToolSpec(
        name="shell",
        description=(
            "Run safe, allowlisted shell commands (df, ps, git, pip, npm, etc.). "
            "Use for system health monitoring, dependency checks, CI report generation."
        ),
        env_vars=[],
    ),
}


# ---------------------------------------------------------------------------
# Helpers — used by planner skill generation
# ---------------------------------------------------------------------------

def data_channel_summary() -> str:
    """One-line description of each data channel for the planner system prompt."""
    lines = []
    for name, spec in CHANNEL_REGISTRY.items():
        if spec.role == "data":
            lines.append(f"  {name:<14} — {spec.description.split('.')[0]}")
    return "\n".join(lines)


def output_channel_summary() -> str:
    """One-line description of each output channel for the planner system prompt."""
    lines = []
    for name, spec in CHANNEL_REGISTRY.items():
        if spec.role == "output":
            lines.append(f"  {name:<14} — {spec.description.split('.')[0]}")
    return "\n".join(lines)


def tool_summary() -> str:
    """One-line description of each agent tool for the planner system prompt."""
    lines = []
    for name, spec in TOOL_REGISTRY.items():
        lines.append(f"  {name:<14} — {spec.description.split('.')[0]}")
    return "\n".join(lines)
