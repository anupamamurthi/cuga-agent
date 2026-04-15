# Use Case: IT Ops Alert Triage

## Why this is a good event-driven use case

In IT operations, alerts fire constantly — CPU spikes, error rate increases, latency jumps, deployment anomalies. Traditionally an on-call engineer:

1. Gets paged
2. Opens dashboards
3. Looks up runbooks
4. Decides whether to escalate or remediate

This is exactly where an event-driven CUGA shines:
- **Alerts are naturally async** — you can't block the monitoring system waiting 30 seconds for LLM analysis
- **Context is structured** — alert payloads already have service name, metric, threshold, region, timestamp
- **Output is actionable** — CUGA can produce a triage summary + recommended action before the engineer even opens their laptop
- **Volume varies wildly** — 0 alerts at 2am, 50 during a deploy; the queue absorbs the spike

## Architecture for this use case

```
Monitoring System (Datadog / PagerDuty / Prometheus Alertmanager)
        │
        │  POST /events   (webhook from alert rule)
        ↓
  cuga-runtime
        │  { trigger: "alert.cpu_high", context: { service, value, region } }
        │
        ↓
  CUGA (sales_assistant or ops_agent persona)
        │  "Analyse this CPU alert. Service: auth-api, CPU: 94%, ..."
        ↓
  Result: triage summary + recommended action
        │
        ├── GET /status/{task_id}         (ops dashboard polls)
        └── POST callback_url             (Slack webhook, PagerDuty note, Jira comment)
```

## Files

| File | What it does |
|------|-------------|
| `alert_simulator.py` | Simulates a monitoring system firing alert events |
| `sample_alerts.py` | Sample alert payloads (CPU, error rate, latency, deployment) |
| `test_it_ops_flow.py` | Integration test + e2e test for this use case |
