# Monitoring Setup

For every new feature, define the four golden signals:

1. **Latency** — p50 / p95 / p99 response times, split by success vs error
2. **Traffic** — requests per second (rps), broken down by endpoint and client
3. **Errors** — error rate (%), categorised by type (4xx client vs 5xx server)
4. **Saturation** — queue depth, CPU/memory headroom, connection pool usage

Alerting rules:
- Page on: error rate > 1% for 5 min, p99 latency > 2× baseline for 5 min
- Warn on: error rate > 0.1%, p95 latency > 1.5× baseline
- All alerts must have a runbook link

Dashboard naming: `<service>/<feature>-overview`
Retention: 30 days high-res, 1 year downsampled.
