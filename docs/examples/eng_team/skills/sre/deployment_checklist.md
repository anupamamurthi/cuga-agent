# Deployment Checklist

Before any production deployment, verify:

**Pre-deploy**
- [ ] Feature flag created and defaulting to OFF
- [ ] Database migrations are backwards-compatible (old code can run against new schema)
- [ ] Config/secrets added to vault and environment
- [ ] Dependent services notified of interface changes
- [ ] Load test run at 2× expected peak traffic

**Deploy**
- [ ] Deploy to staging, run smoke tests
- [ ] Canary rollout: 1% → 10% → 50% → 100% with 15-min bake time at each step
- [ ] Monitor error rate and latency at each step — stop if either exceeds baseline by 10%

**Post-deploy**
- [ ] Verify dashboards show expected signal
- [ ] Confirm alerts are firing correctly (test with synthetic traffic)
- [ ] Update runbook with any new operational notes
