"""
Server Health Monitor — agent, tools, and CugaWatcher.

Architecture
------------
CugaAgent:
  Tools:
    get_system_metrics     — full health snapshot (CPU/RAM/disk/load/uptime)
    list_top_processes     — top-N by CPU or RAM
    check_disk_usage       — directory-level disk breakdown
    find_large_files       — locate files > N MB
    get_service_status     — systemctl / launchctl service status
    run_safe_command       — allowlisted read-only shell commands
  Plugins:
    CugaSkillsPlugin       — injects skills/server_health.md

CugaWatcher (reactive monitoring):
  @watcher.source(every_minutes=POLL_INTERVAL)
    → get_system_metrics()

  @watcher.on(metrics_source, when=has_alerts)
    → agent diagnoses + composes alert
    → EmailChannel or LogChannel delivers it
    → cooldown prevents alert spam

  @watcher.on(metrics_source, when=is_critical)
    → same as above but fires immediately (no cooldown check)
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
_SKILLS_DIR  = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Thresholds (mirrored from metrics.py for the watcher predicates)
_DISK_WARN = float(os.getenv("DISK_THRESHOLD", "80"))
_CPU_WARN  = float(os.getenv("CPU_THRESHOLD",  "75"))
_RAM_WARN  = float(os.getenv("RAM_THRESHOLD",  "80"))
_DISK_CRIT = float(os.getenv("DISK_CRITICAL",  "90"))
_CPU_CRIT  = float(os.getenv("CPU_CRITICAL",   "90"))
_RAM_CRIT  = float(os.getenv("RAM_CRITICAL",   "92"))

# How long (seconds) to wait before re-alerting on the same condition
_ALERT_COOLDOWN_S = int(os.getenv("ALERT_COOLDOWN_SECONDS", str(15 * 60)))  # 15 min


# ---------------------------------------------------------------------------
# LangChain tools (wrappers over metrics.py — pure, testable)
# ---------------------------------------------------------------------------

def _make_tools():
    from langchain_core.tools import tool
    import json
    from metrics import (
        get_system_metrics as _get_metrics,
        list_top_processes as _top_procs,
        check_disk_usage   as _disk_usage,
        find_large_files   as _large_files,
        get_service_status as _svc_status,
    )

    @tool
    def get_system_metrics() -> str:
        """
        Return a full health snapshot: CPU %, RAM %, disk %, load averages,
        uptime, severity (ok/warning/critical), and list of active alerts.
        Always call this first for any health check.
        """
        return json.dumps(_get_metrics())

    @tool
    def list_top_processes(by: str = "cpu", n: int = 10) -> str:
        """
        Return the top-N processes sorted by CPU or memory usage.

        Args:
            by: "cpu" (default) or "memory"
            n:  Number of processes to return (default 10)
        """
        return json.dumps(_top_procs(by=by, n=n))

    @tool
    def check_disk_usage(path: str = "/") -> str:
        """
        Return disk usage of direct subdirectories under `path`.
        Use this when disk is high to identify which directory is the biggest.

        Args:
            path: Directory to inspect (default "/")
        """
        return json.dumps(_disk_usage(path=path))

    @tool
    def find_large_files(path: str = "/", min_mb: int = 100, top_n: int = 20) -> str:
        """
        Find files larger than `min_mb` MB under `path`.
        Use this to identify specific large files when disk is high.

        Args:
            path:   Root path to search (default "/")
            min_mb: Minimum file size in MB (default 100)
            top_n:  Max results to return (default 20)
        """
        return json.dumps(_large_files(path=path, min_mb=min_mb, top_n=top_n))

    @tool
    def get_service_status(service: str) -> str:
        """
        Return the status of a named system service via systemctl (Linux) or
        launchctl (macOS). Only services in the ALLOWED_SERVICES env var are checked.

        Args:
            service: Service name, e.g. "nginx", "postgres", "redis"
        """
        return json.dumps(_svc_status(service=service))

    @tool
    def run_safe_command(cmd: str) -> str:
        """
        Run a read-only diagnostic shell command from the allowlist.

        Allowed commands:
            df -h           — disk free summary
            du -sh <path>   — directory size
            uptime          — load average + uptime
            free -h         — memory summary (Linux)
            ps aux          — process list
            netstat -tlnp   — listening ports
            iostat          — disk I/O stats (if installed)
            vmstat          — virtual memory stats

        Any command not starting with an allowed prefix is rejected.

        Args:
            cmd: The exact shell command to run (no pipes, no semicolons)
        """
        import shlex
        import subprocess

        ALLOWED_PREFIXES = (
            "df ", "df\t", "df\n", "df",
            "du ",
            "uptime",
            "free",
            "ps ",
            "netstat",
            "iostat",
            "vmstat",
            "top -b",
            "top -l",
        )
        cmd = cmd.strip()
        if not any(cmd.startswith(p) for p in ALLOWED_PREFIXES):
            return json.dumps({
                "error": f"Command not in allowlist: {cmd!r}. "
                         f"Allowed prefixes: {ALLOWED_PREFIXES}"
            })

        # Block shell metacharacters
        for ch in (";", "&&", "||", "|", "`", "$", ">", "<", "\n"):
            if ch in cmd:
                return json.dumps({"error": f"Shell metacharacter {ch!r} not allowed."})

        try:
            result = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True, timeout=10,
            )
            return json.dumps({
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:500],
                "returncode": result.returncode,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    return [
        get_system_metrics,
        list_top_processes,
        check_disk_usage,
        find_large_files,
        get_service_status,
        run_safe_command,
    ]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=_make_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )


# ---------------------------------------------------------------------------
# CugaWatcher — reactive monitoring
# ---------------------------------------------------------------------------

def make_watcher(agent):
    """
    Build a CugaWatcher that polls system metrics and fires alerts when
    thresholds are exceeded.

    Two handlers on the same source with different predicates:
      handle_warning  — warning or critical severity, cooldown-throttled
      handle_critical — critical only, separate cooldown (fires immediately)

    Cooldown and delivery fallback (email → log) are handled by the framework:
      cooldown_seconds= on @watcher.on  (no manual timestamp tracking)
      smart_deliver()                   (no manual if/else on SMTP config)
    """
    import json
    from cuga_watcher import CugaWatcher
    from cuga_channels import smart_deliver
    from metrics import get_system_metrics, has_alerts

    watcher      = CugaWatcher(agent=agent)
    poll_minutes = float(os.getenv("POLL_INTERVAL_MINUTES", "1"))

    # ── Source: poll system metrics every N minutes ──────────────────────────
    @watcher.source(every_minutes=poll_minutes, name="system_metrics")
    async def collect_metrics():
        m = get_system_metrics()
        log.debug(
            "Metrics — cpu=%.1f%% ram=%.1f%% disk=%.1f%% severity=%s",
            m.get("cpu_pct", 0), m.get("ram_pct", 0),
            m.get("disk_pct", 0), m.get("severity", "?"),
        )
        return m if has_alerts(m) else None   # None → silently dropped

    # ── Handler A: warning-level — cooldown_seconds prevents alert spam ───────
    @watcher.on(
        collect_metrics,
        when=lambda m: m.get("severity") in ("warning", "critical"),
        cooldown_seconds=_ALERT_COOLDOWN_S,
    )
    async def handle_warning(metrics: dict):
        log.info("Alert firing — severity=%s alerts=%s",
                 metrics.get("severity"), metrics.get("alerts"))
        message = (
            f"Server health alert triggered. Current metrics:\n\n"
            f"{json.dumps(metrics, indent=2)}\n\n"
            f"Diagnose the issues, identify top offenders (use list_top_processes "
            f"or check_disk_usage as needed), and compose a concise alert report."
        )
        result = await agent.invoke(message, thread_id="server-alert")
        await smart_deliver(
            result.answer,
            subject_prefix="⚠️ Server Alert",
            metadata={"subject": f"⚠️ Server Alert: {metrics.get('hostname', 'server')}"},
        )

    # ── Handler B: critical — separate cooldown, fires independently ──────────
    @watcher.on(
        collect_metrics,
        when=lambda m: m.get("severity") == "critical",
        cooldown_seconds=_ALERT_COOLDOWN_S,
    )
    async def handle_critical(metrics: dict):
        log.warning("CRITICAL alert — alerts=%s", metrics.get("alerts"))
        message = (
            f"CRITICAL server health alert. Metrics:\n\n"
            f"{json.dumps(metrics, indent=2)}\n\n"
            f"This is urgent. Diagnose the root cause immediately, identify the "
            f"top offenders, and compose an urgent alert report."
        )
        result = await agent.invoke(message, thread_id="server-alert-critical")
        await smart_deliver(
            result.answer,
            subject_prefix="🚨 CRITICAL Server Alert",
            metadata={"subject": f"🚨 CRITICAL Server Alert: {metrics.get('hostname', 'server')}"},
        )

    return watcher


# ---------------------------------------------------------------------------
# Morning briefing factory  (used by CugaHost / RuntimeFactory)
# ---------------------------------------------------------------------------

def make_briefing_agent():
    """
    Returned by the host_factories.py RuntimeFactory for the morning-briefing runtime.
    Same agent as make_agent() — no client needed for briefings.
    """
    return make_agent()
