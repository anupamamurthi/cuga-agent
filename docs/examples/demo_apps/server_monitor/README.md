# Server Monitor — cuga++ demo

An autonomous server health monitor with three concurrent concerns: reactive
alerting when thresholds are crossed, a scheduled morning briefing, and an
always-on chat window for interactive queries.

```
python app.py
python app.py --provider anthropic
python app.py --no-chat          # watcher + briefing only (headless)
python app.py --no-briefing      # watcher + chat only
```

---

## What kind of app this is

This app runs **three things at the same time**, all sharing a single agent:

| Concern | What triggers it | Who's present |
|---|---|---|
| Reactive alerting | Metric threshold crossed (CPU/RAM/disk) | Nobody — fully autonomous |
| Morning briefing | Cron at 08:00 | Nobody — fully autonomous |
| Interactive chat | User asks a question | User is present |

The first two run in the background with no user involved. The third is on-demand.
This is what separates it from video_qa (always user-driven) and newsletter
(user configures it, then leaves).

---

## Personas

### Developer

Owns all of it. Ships the app. Configures thresholds and delivery defaults.

- `agent.py` — CugaAgent with 6 tools, `CugaWatcher` with two alert handlers
- `metrics.py` — pure Python metric collection (psutil + stdlib fallbacks)
- `host_factories.py` — wires the morning briefing runtime for CugaHost
- `cuga_pipelines.yaml` — declares the briefing pipeline (schedule, output, message)
- `skills/server_health.md` — agent behaviour: tool usage order, alert formats,
  safety rules (never suggest `rm`, never kill PIDs autonomously)
- `app.py` — single entry point that starts all three concerns together

### End user (two modes)

**Headless / ops mode** — the app runs on a server, nobody is watching. Alerts
and briefings arrive by email. The user never opens a terminal.

**Interactive mode** — open `http://localhost:8767` in a browser:

```
You: what's eating my disk?
Agent: /var/log is using 48 GB. Largest files: nginx-access.log (12 GB),
       postgres.log (8 GB). Recommend reviewing log rotation settings.

You: is nginx running?
Agent: nginx is active (running). Last 5 log lines show normal 200/304 responses.

You: why is the server slow?
Agent: Load average 1m=6.2 on 4 CPUs (1.55×). Top offender: PID 18432
       (postgres) at 94% CPU. Appears to be running a long query.
```

The chat shares the same agent and tools as the alert/briefing system. The user
can ask anything the agent can answer with its tool set.

---

## Three concerns, one agent

```
┌────────────────────────────────────────────────────────────┐
│  app.py  — starts everything                               │
│                                                            │
│  ┌──────────────────┐  ┌───────────────┐  ┌─────────────┐ │
│  │  CugaWatcher     │  │  CugaHost     │  │ Conversation│ │
│  │  (reactive)      │  │  (scheduled)  │  │ Gateway     │ │
│  │                  │  │               │  │ (chat UI)   │ │
│  │  polls metrics   │  │  CronChannel  │  │             │ │
│  │  every 1 min     │  │  fires 08:00  │  │  browser    │ │
│  │       ↓          │  │       ↓       │  │  :8767      │ │
│  │  threshold? →    │  │  briefing →   │  │      ↓      │ │
│  │  agent.invoke()  │  │  agent.invoke │  │  agent.ask  │ │
│  └──────────────────┘  └───────────────┘  └─────────────┘ │
│              ↓                  ↓                 ↓        │
│         ┌────────────────────────────────────────┐         │
│         │           CugaAgent                    │         │
│         │  tools: get_system_metrics             │         │
│         │          list_top_processes            │         │
│         │          check_disk_usage              │         │
│         │          find_large_files              │         │
│         │          get_service_status            │         │
│         │          run_safe_command              │         │
│         │  skills: server_health.md              │         │
│         └────────────────────────────────────────┘         │
│                           ↓                                │
│              EmailChannel / LogChannel                     │
└────────────────────────────────────────────────────────────┘
```

---

## Reactive alerting — how it works

`CugaWatcher` runs a polling loop every minute. It calls `get_system_metrics()`
in pure Python (no LLM). If all metrics are within thresholds, the result is
silently dropped — no agent, no cost.

Two handlers fire on different conditions:

**Warning handler** — fires when severity is `warning` or `critical`, with a
15-minute cooldown. Prevents alert spam during a sustained high-CPU period.

**Critical handler** — fires only when severity is `critical`, with its own
independent cooldown. Runs immediately regardless of whether the warning handler
already fired.

```
[every 1 min]
metrics = get_system_metrics()    ← pure Python, no LLM

if severity == "ok":
    drop silently                 ← no agent call

if severity == "warning" or "critical":
    if cooldown not active:
        agent.invoke("CPU at 87%... diagnose and compose alert")
        → agent calls list_top_processes(by="cpu")
        → composes formatted alert report
        → smart_deliver() → email or log

if severity == "critical":
    if critical cooldown not active:
        agent.invoke("CRITICAL: CPU at 94%... urgent diagnosis needed")
        → same flow, different urgency framing
```

The key design: **metrics collection is always Python, never LLM**. The LLM
only runs when there's something worth diagnosing.

---

## Morning briefing — how it works

`CugaHost` manages a `CugaRuntime` with a `CronChannel` (default: 08:00 daily).
When it fires, the agent receives:

> "Good morning! Please give me the daily server health briefing. Check current
> metrics, verify any running services you know about, and summarise the overall
> health of the system."

The agent calls `get_system_metrics()`, then `get_service_status()` for known
services, and composes a formatted briefing. Output goes to `EmailChannel`
(if SMTP is configured) or `LogChannel`.

This is separate from the reactive watcher. The briefing fires on a fixed
schedule regardless of whether anything is wrong.

---

## Safety constraints

The agent is intentionally limited in what it can do. All constraints live in
`skills/server_health.md` and the tool implementations:

- `run_safe_command` enforces an allowlist — only `df`, `du`, `uptime`, `free`,
  `ps`, `netstat`, `iostat`, `vmstat` are permitted. Pipes and shell
  metacharacters are blocked.
- `get_service_status` enforces an allowlist — only services named in
  `ALLOWED_SERVICES` can be queried.
- The skill file explicitly tells the agent: never suggest `rm`, never kill PIDs,
  never restart a database without human confirmation. If something looks broken,
  report it and let the human decide.

The agent is a diagnostician, not an operator.

---

## Configuration

All configuration is via environment variables — no YAML editing required.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | auto-detect | `rits` \| `anthropic` \| `openai` \| `watsonx` \| etc. |
| `LLM_MODEL` | provider default | Model name override |
| `ALERT_TO` | none | Email address for alerts and briefings |
| `SMTP_USERNAME` | none | SMTP sender address |
| `SMTP_PASSWORD` | none | SMTP password |
| `POLL_INTERVAL_MINUTES` | `1` | How often to collect metrics |
| `ALERT_COOLDOWN_SECONDS` | `900` | Min seconds between repeated alerts (15 min) |
| `BRIEFING_SCHEDULE` | `0 8 * * *` | Cron expression for morning briefing |
| `DISK_THRESHOLD` / `DISK_CRITICAL` | `80` / `90` | Disk % thresholds |
| `CPU_THRESHOLD` / `CPU_CRITICAL` | `75` / `90` | CPU % thresholds |
| `RAM_THRESHOLD` / `RAM_CRITICAL` | `80` / `92` | RAM % thresholds |
| `ALLOWED_SERVICES` | `nginx,postgres,redis,docker,sshd,cron` | Services the agent may query |

If `ALERT_TO` / SMTP vars are not set, all output goes to stdout via `LogChannel`.

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Entry point — starts watcher, briefing runtime, and chat UI together |
| `agent.py` | CugaAgent with 6 tools + `CugaWatcher` factory with two alert handlers |
| `metrics.py` | Pure Python metric collection — psutil with stdlib fallbacks |
| `host_factories.py` | Registers briefing factory with CugaHost |
| `cuga_pipelines.yaml` | Briefing pipeline declaration (schedule, message, output type) |
| `skills/server_health.md` | Agent behaviour, tool usage order, report formats, safety rules |

---

## Dependencies

```bash
pip install psutil          # strongly recommended — stdlib fallbacks exist but are limited
pip install fastapi uvicorn # required for the chat UI (--no-chat skips this)
```
