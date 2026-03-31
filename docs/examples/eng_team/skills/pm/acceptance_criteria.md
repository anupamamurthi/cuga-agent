# Acceptance Criteria

Write acceptance criteria using Given / When / Then (Gherkin-style):

```
Given [precondition]
When  [action taken]
Then  [expected outcome]
```

Rules:
- Minimum 3 ACs per story; complex features need 5+
- Each AC must be independently verifiable
- Include at least one negative case (e.g. invalid input, unauthorised access)
- Performance ACs must state concrete numbers (e.g. "responds within 200 ms at p99")
- Avoid implementation details — describe behaviour, not code
