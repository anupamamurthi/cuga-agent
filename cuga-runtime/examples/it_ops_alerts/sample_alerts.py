"""
Sample IT operations alert payloads.

In production these would come from Datadog webhooks, PagerDuty rules,
Prometheus Alertmanager, or any monitoring tool that can POST HTTP.

Each alert maps to a CugaTaskEvent:
  trigger  → alert rule name  (useful for routing, logging, dashboards)
  context  → the alert payload (structured data CUGA reasons about)
  query    → the standing instruction for CUGA (same for all alerts of this type)
"""

# The standing prompt for ops triage — same for all alerts.
# A CUGA persona for ops would have this baked in, but it's shown here
# explicitly so the example is self-contained.
OPS_TRIAGE_PROMPT = (
    "You are an experienced SRE triaging a production alert. "
    "Based on the alert context provided, give:\n"
    "1. Severity assessment (P1/P2/P3) with one-sentence justification\n"
    "2. Most likely root cause (top 2 candidates)\n"
    "3. Immediate action: what should the on-call engineer do RIGHT NOW\n"
    "4. Escalation: yes/no, and to whom if yes\n"
    "Keep your response concise — the on-call engineer needs to act in under 2 minutes."
)


# ── Alert payloads ────────────────────────────────────────────────────────────
# Each entry is the `context` field in the CugaTaskEvent.

CPU_HIGH = {
    "trigger": "alert.cpu_high",
    "context": {
        "alert_name": "CPU utilisation > 90%",
        "service": "auth-api",
        "region": "us-east-1",
        "instance": "i-0a1b2c3d",
        "current_value": "94%",
        "threshold": "90%",
        "duration": "8 minutes",
        "recent_deployments": "auth-api v2.4.1 deployed 22 minutes ago",
        "dependent_services": "checkout-api, user-service, admin-portal",
    },
}

ERROR_RATE_SPIKE = {
    "trigger": "alert.error_rate_spike",
    "context": {
        "alert_name": "HTTP 5xx error rate > 5%",
        "service": "payment-service",
        "region": "eu-west-1",
        "current_error_rate": "14.2%",
        "threshold": "5%",
        "affected_endpoints": "/api/v2/charge, /api/v2/refund",
        "duration": "3 minutes",
        "upstream_dependencies": "stripe-gateway (status: degraded per status page)",
        "recent_deployments": "no deployments in last 24h",
    },
}

LATENCY_SPIKE = {
    "trigger": "alert.latency_spike",
    "context": {
        "alert_name": "P99 latency > 2000ms",
        "service": "search-api",
        "region": "ap-southeast-1",
        "current_p99_ms": "3400",
        "threshold_ms": "2000",
        "duration": "15 minutes",
        "database": "elasticsearch cluster es-prod-01",
        "db_cpu": "78%",
        "db_heap_used": "91%",
        "recent_index_activity": "full re-index triggered 20 minutes ago by data-pipeline-job",
    },
}

DEPLOYMENT_ANOMALY = {
    "trigger": "alert.deployment_anomaly",
    "context": {
        "alert_name": "Error rate increased after deployment",
        "service": "recommendation-engine",
        "region": "us-west-2",
        "deployment": "v3.1.0 rolled out at 14:32 UTC (18 minutes ago)",
        "pre_deploy_error_rate": "0.3%",
        "post_deploy_error_rate": "4.8%",
        "rollback_available": "yes — v3.0.9 is the previous stable version",
        "feature_flags": "new_model_serving_enabled=true (introduced in v3.1.0)",
        "on_call_engineer": "jamie@example.com",
    },
}


ALL_ALERTS = [CPU_HIGH, ERROR_RATE_SPIKE, LATENCY_SPIKE, DEPLOYMENT_ANOMALY]
