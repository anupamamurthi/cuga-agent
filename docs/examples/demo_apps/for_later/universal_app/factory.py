"""
Dynamic RuntimeFactory builder.

Takes a pipeline spec dict (captured by the planner tools) and returns a
factory callable that CugaHost can register and call to produce CugaRuntime
instances.

The spec dict has this shape:
    {
      "pipeline_id":      str,           # unique slug, e.g. "arxiv-monitor"
      "description":      str,           # human-readable task description
      "data_type":        str,           # "rss"|"imap"|"slack_data"|"telegram_data"|
                                         # "discord_data"|"docling"|"audio"|"none"
      "trigger_schedule": str,           # cron expression, e.g. "0 8 * * *"
      "output_type":      str,           # "email"|"slack"|"telegram"|"discord"|"sms"|"log"
      "tool_names":       list[str],     # ["web_search", "github", ...]
      "require_buffer":   bool,

      # data-channel-specific (present only when relevant):
      "rss_sources":      list[str],
      "rss_keywords":     list[str],
      "imap_host":        str,
      "imap_username":    str,
      "imap_folder":      str,
      "slack_channel":    str,
      "allowed_user_ids": list[int],
      "discord_channel_id": str,
      "watch_folder":     str,
      "whisper_model":    str,
      "poll_minutes":     float,

      # output-specific:
      "output_target":    str,           # email addr, Slack channel, Telegram chat ID, ...
      "output_subject":   str,           # email subject prefix
    }

Both spec and runtime config are merged on each factory call, so CugaHost can
update a running pipeline by passing a partial override dict.
"""
from __future__ import annotations

import os
from typing import Any


def build_factory(spec: dict) -> Any:
    """
    Return a factory callable ``(config: dict) -> CugaRuntime``.

    The returned callable is safe to register with ``CugaHost.register_factory()``
    and will be called with the stored config dict on every (re)start.
    """
    # Capture provider/model at creation time so the pipeline agent uses the
    # same LLM that was active when the user issued the command.
    _provider = os.getenv("LLM_PROVIDER")
    _model    = os.getenv("LLM_MODEL")

    def factory(config: dict) -> Any:
        # Merge: spec is the baseline, config can override any key at restart
        merged = {**spec, **config}
        return _build_runtime(merged, _provider, _model)

    return factory


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_runtime(merged: dict, provider: str | None, model: str | None) -> Any:
    from cuga_channels import (
        CronChannel, LogChannel, EmailChannel, SlackChannel,
        TelegramChannel, DiscordChannel, SMSChannel,
        RssChannel, DoclingChannel, AudioChannel,
        IMAPChannel, SlackDataChannel,
        TelegramDataChannel, DiscordDataChannel,
        CugaRuntime,
    )

    agent = _build_agent(merged, provider, model)

    # --- Input channels ---------------------------------------------------
    input_channels = []
    data_type = merged.get("data_type", "none")

    if data_type == "rss":
        sources  = merged.get("rss_sources") or []
        keywords = merged.get("rss_keywords") or []
        input_channels.append(RssChannel(
            sources=sources,
            keywords=keywords or None,
            poll_minutes=float(merged.get("poll_minutes", 15)),
        ))

    elif data_type == "imap":
        input_channels.append(IMAPChannel(
            host=merged.get("imap_host", ""),
            username=merged.get("imap_username", ""),
            password=os.getenv("IMAP_PASSWORD", ""),
            folder=merged.get("imap_folder", "INBOX"),
            poll_minutes=float(merged.get("poll_minutes", 5)),
        ))

    elif data_type == "slack_data":
        input_channels.append(SlackDataChannel(
            token=os.getenv("SLACK_BOT_TOKEN", ""),
            channel=merged.get("slack_channel", ""),
            poll_minutes=float(merged.get("poll_minutes", 2)),
        ))

    elif data_type == "telegram_data":
        input_channels.append(TelegramDataChannel(
            token=os.getenv("TELEGRAM_BOT_TOKEN"),
            allowed_user_ids=merged.get("allowed_user_ids"),
        ))

    elif data_type == "discord_data":
        input_channels.append(DiscordDataChannel(
            token=os.getenv("DISCORD_BOT_TOKEN"),
            channel_id=merged.get("discord_channel_id"),
            poll_seconds=float(merged.get("poll_seconds", 10)),
        ))

    elif data_type == "docling":
        input_channels.append(DoclingChannel(
            watch_dir=merged.get("watch_folder", "./watched"),
            poll_minutes=float(merged.get("poll_minutes", 1)),
        ))

    elif data_type == "audio":
        input_channels.append(AudioChannel(
            watch_dir=merged.get("watch_folder", "./recordings"),
            model=merged.get("whisper_model", "base"),
        ))

    # Trigger (cron — always present)
    trigger_schedule = merged.get("trigger_schedule", "0 8 * * *")
    trigger_message  = _build_trigger_message(merged)
    input_channels.append(CronChannel(
        schedule=trigger_schedule,
        message=trigger_message,
    ))

    # --- Output channels --------------------------------------------------
    output_type    = merged.get("output_type", "log")
    output_target  = merged.get("output_target", "")
    output_subject = merged.get("output_subject", "CUGA Digest")

    out_ch = _build_output_channel(
        output_type, output_target, output_subject,
        EmailChannel, SlackChannel, TelegramChannel,
        DiscordChannel, SMSChannel,
    )
    output_channels = [out_ch, LogChannel()] if out_ch else [LogChannel()]

    return CugaRuntime(
        agent=agent,
        input_channels=input_channels,
        output_channels=output_channels,
        thread_id=merged["pipeline_id"],
        require_buffer=merged.get("require_buffer", data_type != "none"),
    )


def _build_agent(merged: dict, provider: str | None, model: str | None) -> Any:
    """Build a CugaAgent with the tool set specified in the pipeline spec."""
    import sys
    from pathlib import Path

    # Ensure demo helpers (_llm.py) are on the path
    _demos_dir = Path(__file__).parent.parent
    if str(_demos_dir) not in sys.path:
        sys.path.insert(0, str(_demos_dir))

    from cuga import CugaAgent
    from _llm import create_llm

    tools = _build_tools(merged.get("tool_names") or [])

    return CugaAgent(
        model=create_llm(provider=provider, model=model),
        tools=tools,
        cuga_folder=str(Path(__file__).parent / ".cuga"),
    )


def _build_tools(tool_names: list[str]) -> list:
    """Instantiate the requested tool factories and return flat list of tools."""
    from cuga_channels import (
        make_web_search_tool, make_calendar_tools, make_github_tools,
        make_market_data_tools, make_image_generation_tool,
        make_rag_tools, make_shell_tools,
    )

    tools = []
    for name in tool_names:
        if name == "web_search":
            tools.append(make_web_search_tool())
        elif name == "calendar":
            tools.extend(make_calendar_tools())
        elif name == "github":
            tools.extend(make_github_tools())
        elif name == "market_data":
            tools.extend(make_market_data_tools())
        elif name == "image_gen":
            tools.extend(make_image_generation_tool())
        elif name == "rag":
            tools.extend(make_rag_tools())
        elif name == "shell":
            tools.extend(make_shell_tools())
    return tools


def _build_trigger_message(merged: dict) -> str:
    """
    Build the message that is injected into the agent when the cron fires.

    This is the sole task-specific prompt for the pipeline agent — it tells the
    agent what to do, what data it has (if any), and how to format its output.
    """
    description = merged.get("description", "Complete the scheduled pipeline task.")
    data_type   = merged.get("data_type", "none")
    tool_names  = merged.get("tool_names") or []
    output_type = merged.get("output_type", "log")

    lines = [
        "You are running a scheduled pipeline task.",
        "",
        f"Task: {description}",
        "",
    ]

    # Data context
    if data_type != "none":
        lines += [
            "You have been given a list of collected items (above) from the data channel.",
            "Analyse them, select the most relevant and significant ones, and produce a",
            "well-structured, human-readable summary or digest.",
            "",
        ]
    elif tool_names:
        tool_list = ", ".join(tool_names)
        lines += [
            f"Use your tools ({tool_list}) to gather the information needed to complete the task.",
            "",
        ]

    # Output formatting hint
    if output_type == "email":
        lines += [
            "Format your output as styled HTML suitable for an email digest.",
            "Use headings, bullet points, and links. Keep it concise and scannable.",
        ]
    elif output_type in ("slack", "discord", "telegram"):
        lines += [
            "Format your output as clean plain text or Markdown.",
            "Keep it brief and easy to read in a chat interface.",
        ]
    elif output_type == "sms":
        lines += [
            "Format your output as a very short plain-text summary (under 160 characters).",
        ]
    else:
        lines += [
            "Format your output clearly. Your full response will be delivered automatically.",
        ]

    lines += [
        "",
        "Do not call any send, email, or delivery tools — delivery is handled automatically.",
    ]

    return "\n".join(lines)


def _build_output_channel(
    output_type: str,
    output_target: str,
    output_subject: str,
    EmailChannel, SlackChannel, TelegramChannel, DiscordChannel, SMSChannel,
):
    """Return an instantiated OutputChannel, or None (falls back to LogChannel)."""
    if output_type == "email" and output_target:
        return EmailChannel(
            to=output_target,
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            subject_prefix=output_subject,
        )

    if output_type == "slack" and output_target:
        is_webhook = output_target.startswith("http")
        return SlackChannel(
            webhook_url=output_target if is_webhook else None,
            token=os.getenv("SLACK_BOT_TOKEN") if not is_webhook else None,
            channel=output_target if not is_webhook else None,
        )

    if output_type == "telegram" and output_target:
        return TelegramChannel(
            token=os.getenv("TELEGRAM_BOT_TOKEN"),
            chat_id=output_target,
        )

    if output_type == "discord" and output_target:
        is_webhook = output_target.startswith("http")
        return DiscordChannel(
            webhook_url=output_target if is_webhook else None,
            token=os.getenv("DISCORD_BOT_TOKEN") if not is_webhook else None,
            channel_id=output_target if not is_webhook else None,
        )

    if output_type == "sms" and output_target:
        return SMSChannel(
            to=output_target,
            account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            from_=os.getenv("TWILIO_FROM"),
        )

    return None  # caller falls back to LogChannel
