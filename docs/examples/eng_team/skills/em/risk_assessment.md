# Risk Assessment

Score every risk on two axes (1-5 each):
- **Likelihood** (1 = rare, 5 = almost certain)
- **Impact** (1 = negligible, 5 = critical / user-facing outage)

Risk score = Likelihood × Impact

| Score | Action |
|---|---|
| 1–5 | Accept / monitor |
| 6–14 | Mitigate — add to sprint backlog |
| 15–25 | Block ship until mitigated |

For each risk above 5, define:
- **Mitigation**: what reduces likelihood or impact
- **Owner**: single person accountable
- **Trigger**: what event escalates this risk

Always produce a summary table: Risk | Score | Mitigation | Owner
