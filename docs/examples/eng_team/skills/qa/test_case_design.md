# Test Case Design

Structure every test case with:

- **ID**: TC-001, TC-002, ...
- **Title**: Short description of what is tested
- **Type**: Unit | Integration | E2E | Performance | Security
- **Priority**: P0 (blocker) → P3 (nice to have)
- **Preconditions**: System state required before the test
- **Steps**: Numbered, reproducible steps
- **Expected result**: Exact observable outcome
- **Actual result**: (filled during execution)

Rules:
- Each AC from the spec maps to at least one P0 test
- Test names must be self-documenting: `test_rate_limit_returns_429_when_exceeded`
- Parametrize for boundary values (0, max-1, max, max+1)
