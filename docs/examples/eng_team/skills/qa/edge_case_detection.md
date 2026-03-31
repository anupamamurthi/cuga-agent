# Edge Case Detection

Systematically scan for edge cases using these lenses:

**Input boundaries**
- Empty / null / zero / negative values
- Maximum allowed values + 1
- Unicode, special characters, SQL/script injection strings

**Concurrency**
- Two requests modifying the same resource simultaneously
- Race conditions at limit boundaries (e.g. 99th vs 100th request)

**State transitions**
- What happens if the system restarts mid-operation?
- What if a dependency is temporarily unavailable?

**Auth & permissions**
- Unauthenticated requests
- Token expired mid-session
- User with partial permissions

**Time**
- Clock skew between services
- Daylight saving transitions
- Requests arriving in wrong order (out-of-order delivery)
