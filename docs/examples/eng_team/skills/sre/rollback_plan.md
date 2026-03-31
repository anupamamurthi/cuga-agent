# Rollback Plan

Every deployment needs a documented rollback plan before go-live.

**Rollback decision criteria** (any one triggers rollback):
- Error rate exceeds 1% sustained for 5 minutes
- p99 latency > 3× pre-deploy baseline
- Data corruption detected
- On-call engineer judgment

**Rollback steps (template)**:
1. Disable feature flag immediately (target: < 2 min)
2. If feature flag not sufficient: redeploy previous image tag
3. If schema migration was applied: run down-migration script (pre-tested)
4. Notify stakeholders within 10 minutes of decision

**Complexity classification**:
- **Low**: feature flag toggle only — rollback in < 2 min
- **Medium**: redeploy required, no schema change — rollback in < 15 min
- **High**: schema migration involved — rollback in < 1 hour, requires DBA
- **Very High**: cross-service contract change — coordinate rollback, may require downtime
