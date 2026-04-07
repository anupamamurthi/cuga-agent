"""
Planner tools — LangChain @tool functions given to the universal planner agent.

The planner agent has four tools:

  create_pipeline   — takes a full pipeline spec, registers a dynamic factory
                      with the embedded CugaHost, and starts the runtime.

  update_pipeline   — reconfigures a running pipeline (e.g. change schedule
                      or output target) without stopping it.

  stop_pipeline     — stop and remove a running pipeline.

  list_pipelines    — show all currently running pipelines.

All tools close over ``host`` (the embedded CugaHost) and ``client``
(the CugaHostClient). Both are injected at app startup via make_planner_tools().
"""
from __future__ import annotations

import re
from typing import Optional

from langchain_core.tools import tool


def make_planner_tools(host, client) -> list:
    """
    Build the four planner tools, bound to the given host and client instances.

    Parameters
    ----------
    host    CugaHost instance (embedded in-process) — needed to register
            dynamic factories at runtime.
    client  CugaHostClient — used for start/stop/list HTTP calls.

    Returns
    -------
    List of four LangChain @tool functions ready to pass to CugaAgent.
    """

    @tool
    async def create_pipeline(
        description: str,
        data_type: str = "none",
        trigger_schedule: str = "0 8 * * *",
        output_type: str = "log",
        tool_names: Optional[list[str]] = None,
        # RSS params
        rss_sources: Optional[list[str]] = None,
        rss_keywords: Optional[list[str]] = None,
        # Folder-watch params (docling / audio)
        watch_folder: str = "./watched",
        whisper_model: str = "base",
        # Email / IMAP params
        imap_host: str = "",
        imap_username: str = "",
        imap_folder: str = "INBOX",
        # Slack / Discord params
        slack_channel: str = "",
        discord_channel_id: str = "",
        # Output params
        output_target: str = "",
        output_subject: str = "CUGA Digest",
        # Poll interval (data channels)
        poll_minutes: float = 15.0,
    ) -> str:
        """
        Create and start a new automated pipeline.

        This is the primary tool — call it whenever the user wants to start any
        kind of monitoring, digest, alerting, or automation task.

        Parameters
        ----------
        description       What this pipeline does (human-readable, used as the
                          agent prompt when the pipeline runs).
        data_type         Data source. One of:
                            rss          — poll RSS/Atom feeds
                            imap         — watch an email inbox
                            slack_data   — poll a Slack channel
                            telegram_data — receive Telegram messages
                            discord_data — poll a Discord channel
                            docling      — watch a folder for documents (PDF, images, Word)
                            audio        — watch a folder for audio files (transcribed with Whisper)
                            none         — no data source; agent uses tools to fetch its own data
        trigger_schedule  When to run. Cron expression, e.g.:
                            "0 8 * * *"       daily at 8am
                            "0 8 * * 1-5"     weekdays at 8am
                            "0 */4 * * *"     every 4 hours
                            "*/30 * * * *"    every 30 minutes
        output_type       Where to deliver output. One of:
                            email, slack, telegram, discord, sms, log (default: log)
        tool_names        Tools to give the pipeline agent. Supported values:
                            web_search, calendar, github, market_data,
                            image_gen, rag, shell
                          Use for data_type="none" pipelines that fetch their own data.
        rss_sources       RSS feed URLs (required when data_type="rss").
        rss_keywords      Filter keywords for RSS items. Omit to accept all items.
        watch_folder      Folder to watch (required when data_type="docling" or "audio").
        whisper_model     Whisper model size: tiny | base | small | medium | large.
        imap_host         IMAP server hostname (required when data_type="imap").
        imap_username     IMAP username/email (required when data_type="imap").
        imap_folder       Mailbox folder to watch (default: INBOX).
        slack_channel     Slack channel name or ID (required when data_type="slack_data").
        discord_channel_id Discord channel ID (required when data_type="discord_data").
        output_target     Destination: email address, Slack channel, Telegram chat ID,
                          Discord channel ID, or phone number. Required for all
                          output_types except "log".
        output_subject    Subject prefix for email output (default: "CUGA Digest").
        poll_minutes      How often the data channel polls its source (default: 15).
        """
        from factory import build_factory

        pipeline_id = _slugify(description)

        spec = {
            "pipeline_id":       pipeline_id,
            "description":       description,
            "data_type":         data_type,
            "trigger_schedule":  trigger_schedule,
            "output_type":       output_type,
            "tool_names":        tool_names or [],
            # data-channel params
            "rss_sources":       rss_sources or [],
            "rss_keywords":      rss_keywords or [],
            "watch_folder":      watch_folder,
            "whisper_model":     whisper_model,
            "imap_host":         imap_host,
            "imap_username":     imap_username,
            "imap_folder":       imap_folder,
            "slack_channel":     slack_channel,
            "discord_channel_id": discord_channel_id,
            "poll_minutes":      poll_minutes,
            # output params
            "output_target":     output_target,
            "output_subject":    output_subject,
            # runtime behaviour
            "require_buffer":    data_type != "none",
        }

        # Register the dynamic factory with the embedded host, then start it
        factory = build_factory(spec)
        if host is not None:
            host.register_factory(pipeline_id, factory)

        try:
            await client.start_runtime(pipeline_id, pipeline_id, spec)
        except Exception as exc:
            return f"Failed to start pipeline '{pipeline_id}': {exc}"

        output_desc = (
            f"→ {output_type}:{output_target}" if output_target
            else f"→ {output_type} (console)"
        )
        data_desc = (
            f"  Data:     {data_type}" if data_type != "none"
            else f"  Tools:    {', '.join(tool_names or []) or 'none'}"
        )
        return (
            f"Pipeline '{pipeline_id}' started.\n"
            f"{data_desc}\n"
            f"  Schedule: {trigger_schedule}\n"
            f"  Output:   {output_desc}"
        )

    @tool
    async def update_pipeline(
        pipeline_id: str,
        trigger_schedule: Optional[str] = None,
        output_target: Optional[str] = None,
        output_type: Optional[str] = None,
        rss_sources: Optional[list[str]] = None,
        rss_keywords: Optional[list[str]] = None,
    ) -> str:
        """
        Reconfigure a running pipeline without restarting from scratch.

        Only the fields you provide will be changed; all other settings stay the same.

        Parameters
        ----------
        pipeline_id       ID of the pipeline to update (as shown by list_pipelines).
        trigger_schedule  New cron expression (e.g. "0 9 * * *" to move from 8am to 9am).
        output_target     New delivery destination (email, Slack channel, etc.).
        output_type       New output channel type (email, slack, log, etc.).
        rss_sources       Replacement RSS feed URLs.
        rss_keywords      Replacement filter keywords.
        """
        try:
            info = await client.get_runtime(pipeline_id)
        except Exception:
            return f"No pipeline named '{pipeline_id}' is currently running."

        current = info.get("config", {})
        overrides: dict = {}
        if trigger_schedule is not None:
            overrides["trigger_schedule"] = trigger_schedule
        if output_target is not None:
            overrides["output_target"] = output_target
        if output_type is not None:
            overrides["output_type"] = output_type
        if rss_sources is not None:
            overrides["rss_sources"] = rss_sources
        if rss_keywords is not None:
            overrides["rss_keywords"] = rss_keywords

        if not overrides:
            return "No changes specified."

        new_config = {**current, **overrides}
        try:
            await client.update_runtime(pipeline_id, pipeline_id, new_config)
        except Exception as exc:
            return f"Failed to update pipeline '{pipeline_id}': {exc}"

        changes = ", ".join(f"{k}={v!r}" for k, v in overrides.items())
        return f"Pipeline '{pipeline_id}' updated: {changes}"

    @tool
    async def stop_pipeline(pipeline_id: str) -> str:
        """
        Stop a running pipeline.

        Parameters
        ----------
        pipeline_id   ID of the pipeline to stop (as shown by list_pipelines).
                      Use "all" to stop every running pipeline.
        """
        if pipeline_id.lower() == "all":
            try:
                runtimes = await client.list_runtimes()
            except Exception as exc:
                return f"Could not list pipelines: {exc}"
            if not runtimes:
                return "No pipelines are currently running."
            stopped = []
            for rt in runtimes:
                try:
                    await client.stop_runtime(rt["id"])
                    stopped.append(rt["id"])
                except Exception:
                    pass
            return f"Stopped {len(stopped)} pipeline(s): {', '.join(stopped)}"

        try:
            await client.stop_runtime(pipeline_id)
            return f"Pipeline '{pipeline_id}' stopped."
        except Exception:
            return f"No pipeline named '{pipeline_id}' is currently running."

    @tool
    async def list_pipelines() -> str:
        """
        List all currently running pipelines with their configuration summary.
        """
        try:
            runtimes = await client.list_runtimes()
        except Exception as exc:
            return f"Could not reach the pipeline host: {exc}"

        if not runtimes:
            return "No pipelines are currently running."

        lines = [f"{len(runtimes)} pipeline(s) running:\n"]
        for rt in runtimes:
            cfg = rt.get("config", {})
            data_type = cfg.get("data_type", "none")
            sched     = cfg.get("trigger_schedule", "?")
            out_type  = cfg.get("output_type", "log")
            out_tgt   = cfg.get("output_target", "console")
            desc      = cfg.get("description", rt["id"])
            lines.append(
                f"  [{rt['id']}]\n"
                f"    {desc}\n"
                f"    data={data_type}  schedule={sched}  output={out_type}:{out_tgt}"
            )

        return "\n".join(lines)

    return [create_pipeline, update_pipeline, stop_pipeline, list_pipelines]


def _slugify(text: str) -> str:
    """
    Convert a description to a URL-safe pipeline ID.

    Examples
    --------
    "Monitor arxiv for AI papers"  →  "monitor-arxiv-for-ai-papers"
    "Daily Bitcoin price alert"    →  "daily-bitcoin-price-alert"
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    # Cap at 40 chars to keep IDs readable
    return slug[:40].rstrip("-")
