# Server Monitor

A self-contained server health monitor with a browser UI, powered by CugaAgent.

No CugaHost, no cuga-channels, no runtime pipeline — just:
- **CugaAgent** for reasoning over system state
- **FastAPI** for the web UI and REST API
- **psutil** for cross-platform system metrics
- A background asyncio loop for threshold monitoring

---

## Quick start

```bash
cd docs/examples/demo_apps/server_monitor
pip install -r requirements.txt
python main.py
```

Open **http://127.0.0.1:8767**

---

## What you get

### Live Metrics panel
Real-time CPU, RAM, Disk, and load-average gauges. Colour-coded by threshold
(green → yellow → red). Auto-refreshes every 15 seconds; manual refresh in
the header.

### Chat — Ask the Agent
Natural-language chat with a DevOps agent that has full read access to:
- System metrics (`get_system_metrics`)
- Top processes by CPU or memory (`list_top_processes`)
- Directory-level disk breakdown (`check_disk_usage`)
- Large-file finder (`find_large_files`)
- Service status via systemctl / launchctl (`get_service_status`)
- Safe read-only shell commands (`run_safe_command`)

Example questions (also shown as quick-pick chips in the UI):
```
What's the current server health?
What's using the most CPU right now?
What's eating my disk?
Why is the server slow?
Is nginx running?
Find files larger than 500MB
Give me a full health briefing
```

### Alert Log
The background monitor polls metrics every N seconds. When a threshold is
breached and the cooldown has elapsed, the agent diagnoses the issue and the
result appears in the Alert Log. Click any entry to expand the full diagnosis.
Use **Check now** to trigger an immediate check.

### Alert Settings
Configure directly in the UI (persisted to `.store.json`):
- **Poll interval** — seconds between metric checks
- **Cooldown** — minimum seconds between repeated alerts
- **Warn / critical thresholds** for CPU, RAM, and Disk

---

## CLI flags

```bash
python main.py --port 9000
python main.py --provider anthropic
python main.py --provider openai --model gpt-4o
```

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | — | `rits` \| `anthropic` \| `openai` \| `ollama` \| `watsonx` |
| `LLM_MODEL` | — | Model override |
| `POLL_INTERVAL_SECONDS` | `60` | Metric poll frequency |
| `ALERT_COOLDOWN_SECONDS` | `900` | Min seconds between repeated alerts |
| `CPU_THRESHOLD` | `75` | CPU warn % |
| `CPU_CRITICAL` | `90` | CPU critical % |
| `RAM_THRESHOLD` | `80` | RAM warn % |
| `RAM_CRITICAL` | `92` | RAM critical % |
| `DISK_THRESHOLD` | `80` | Disk warn % |
| `DISK_CRITICAL` | `90` | Disk critical % |
| `ALLOWED_SERVICES` | `nginx,postgres,redis,docker,sshd,cron` | Services the agent may query |

Threshold env vars set initial defaults. UI settings (persisted in `.store.json`)
take precedence after the first save.

---

## Files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app: agent, background monitor, REST API, HTML UI |
| `metrics.py` | Pure system metrics functions (psutil + stdlib fallbacks) |
| `skills/server_health.md` | Agent skill: tools, severity levels, report formats |
| `requirements.txt` | Python dependencies |
| `.store.json` | Persisted thresholds + poll settings (created on first save) |

---

## Safety constraints

The agent is a diagnostician, not an operator:
- `run_safe_command` enforces an allowlist (`df`, `du`, `uptime`, `ps`, `netstat`, …). Pipes and shell metacharacters are blocked.
- `get_service_status` only queries services listed in `ALLOWED_SERVICES`.
- The skill file explicitly tells the agent: never suggest `rm`, never kill PIDs, never restart a database without human confirmation.
