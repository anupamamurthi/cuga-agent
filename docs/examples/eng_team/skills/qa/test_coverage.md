# Test Coverage

Coverage targets by test type:

| Type | Target | Minimum |
|---|---|---|
| Unit | 85% line | 70% |
| Integration | Critical paths 100% | All P0 ACs |
| E2E | Happy path + 2 error paths | Happy path only |
| Performance | p50, p95, p99 measured | p99 < SLA |

When reporting coverage:
- Flag any AC with zero test coverage as a blocker
- Call out branches missed (not just lines)
- List the top 3 riskiest untested areas with justification
- Recommend if the feature is ready to ship based on coverage
