# Implementation Patterns

Choose patterns deliberately and justify each choice:

| Pattern | Use when |
|---|---|
| Repository | Abstracting data access layer |
| Strategy | Multiple interchangeable algorithms |
| Middleware / Chain | Cross-cutting concerns (auth, logging, rate-limit) |
| Circuit Breaker | Calling unreliable external services |
| Saga | Distributed transactions across services |

When suggesting an implementation:
1. Name the pattern(s) used
2. Sketch the key interfaces / contracts (not full code)
3. Identify the main integration points with existing code
4. Note migration path if changing existing behaviour
