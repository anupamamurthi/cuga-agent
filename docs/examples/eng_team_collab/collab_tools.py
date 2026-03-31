"""
Collaborative tools for the back-and-forth eng team demo.

These tools are designed for a conversation-style pipeline where agents
can hand work back and forth rather than running in a fixed sequence.
"""
from __future__ import annotations

from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Dev tools
# ---------------------------------------------------------------------------

@tool
def write_implementation(feature: str, spec_summary: str = "") -> str:
    """
    Write an initial implementation design for a feature.

    Args:
        feature: The feature to implement.
        spec_summary: Summary of the PM spec to implement against.
    """
    return f"""IMPLEMENTATION DESIGN v1 — {feature}
──────────────────────────────────────────
Components:
  RateLimitMiddleware   — intercepts requests pre-handler, checks Redis counter
  RateLimitStore        — Redis sliding-window counter (INCR + TTL)
  RateLimitConfig       — per-route limits loaded from environment

Key decisions:
  • Sliding window (not fixed) — fairer under burst traffic
  • Redis INCR for atomic increment — avoids read-then-write race
  • Fail-open on Redis timeout — requests pass through, alert fires

API changes:
  All responses gain headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
  HTTP 429 returned when limit exceeded, with Retry-After header

Integration: middleware registered before auth in app startup
Blast radius: all API routes (read-only, no logic change to handlers)

{('Spec reference: ' + spec_summary[:200]) if spec_summary else ''}
"""


@tool
def fix_bug(bug_report: str, version: str = "v1") -> str:
    """
    Fix a reported bug and describe exactly what changed.

    Args:
        bug_report: The bug report from QA.
        version: The version being fixed (e.g. v1, v2).
    """
    report_lower = bug_report.lower()

    if "race" in report_lower or "concurrent" in report_lower or "atomic" in report_lower:
        fix = """BUG FIX: Race condition at limit boundary
──────────────────────────────────────────
Root cause: Used GET then INCR — two concurrent requests could both read
  counter = N-1 (below limit), both increment, both succeed → limit exceeded by 1.

Fix applied:
  Before: counter = redis.get(key); if counter < limit: redis.incr(key)
  After:  counter = redis.incr(key); if counter > limit: raise RateLimitExceeded

  This makes the increment atomic. The first request over the limit gets 429.
  Added: redis.expire(key, window_seconds) after first INCR to set TTL.

Test to verify: TC-008 concurrent requests at limit boundary — now deterministic."""

    elif "429" in report_lower or "error" in report_lower or "status" in report_lower:
        fix = """BUG FIX: Missing / incorrect error response format
──────────────────────────────────────────
Root cause: 429 response body was empty — client had no retry guidance.

Fix applied:
  Added structured error body:
  {
    "code": "rate_limit_exceeded",
    "message": "Too many requests. Please retry after the reset time.",
    "retry_after": <unix_timestamp>,
    "request_id": "<uuid>"
  }
  Added Retry-After header (RFC 7231 compliant) pointing to window reset time.

Test to verify: TC-002 invalid/limit-exceeded path — now returns full error body."""

    elif "migration" in report_lower or "schema" in report_lower or "database" in report_lower:
        fix = """ADDITION: Database migration plan
──────────────────────────────────────────
No schema changes required — rate limiting is entirely Redis-based.

Redis key structure: rate_limit:{client_id}:{window_start}
TTL: window_seconds (auto-expires, no manual cleanup needed)
Migration steps:
  1. Deploy code with feature flag OFF (no Redis keys written)
  2. Confirm Redis cluster has headroom (rate-limit keys are small: ~50 bytes each)
  3. Enable feature flag at 1% → monitor Redis memory and hit rate
  4. Scale to 100% over 24 hours

Rollback: disable feature flag — Redis keys expire naturally within one window."""

    else:
        fix = f"""BUG FIX: {bug_report[:80]}
──────────────────────────────────────────
Root cause identified and patched in implementation {version}.
Specific change: added validation, boundary check, and error handling
  for the reported scenario.
Regression test added to cover this exact case."""

    return fix


@tool
def explain_design_decision(decision: str) -> str:
    """
    Explain a specific design decision in the implementation.

    Args:
        decision: The design decision being questioned.
    """
    return f"""DESIGN DECISION EXPLANATION — {decision}
──────────────────────────────────────────
Choice made: sliding-window counter in Redis
Alternatives considered:
  • Fixed window: simpler but allows 2× burst at window boundaries
  • Token bucket: smoother but harder to distribute across Redis cluster
  • In-process: fast but doesn't work with multiple API server instances

Why this choice:
  Sliding window + Redis works correctly with N API servers (shared state),
  handles burst traffic fairly, and Redis INCR is O(1) at any scale.
  Operational cost: one Redis call per request — acceptable given existing cluster.

Trade-off acknowledged: Redis as a dependency in the hot path.
Mitigation: fail-open on Redis timeout (requests pass through, alert fires).
"""


# ---------------------------------------------------------------------------
# QA tools
# ---------------------------------------------------------------------------

@tool
def review_implementation(implementation_summary: str, review_round: int = 1) -> str:
    """
    Review an implementation and return findings.

    Args:
        implementation_summary: Summary of what was implemented.
        review_round: Which review round this is (1 = first, 2 = re-review after fix, etc.)
    """
    if review_round == 1:
        return f"""CODE REVIEW — Round {review_round}
──────────────────────────────────────────
Reviewed: {implementation_summary[:120]}

BUGS FOUND: 2

BUG-001 [P0] Race condition at limit boundary
  Description: Implementation uses GET then INCR (two separate Redis calls).
    Two concurrent requests arriving when counter = limit-1 will both read
    counter < limit, both increment, both succeed — limit is violated.
  Reproduction: Send (limit) concurrent requests simultaneously.
  Expected: exactly (limit) succeed, rest get 429.
  Actual: up to (limit + concurrency) succeed.
  Fix needed: use atomic Redis INCR, check value after increment.

BUG-002 [P1] 429 response body is empty
  Description: When rate limit is exceeded, response body is empty.
    Client has no way to know when to retry.
  Expected: structured JSON body with retry_after timestamp and request_id.
  Actual: empty body, no Retry-After header.
  Fix needed: add error body + Retry-After header per RFC 7231.

PASSED:
  ✓ X-RateLimit-* headers present on all responses
  ✓ Feature flag integration correct
  ✓ Middleware registration order correct (before auth)
"""
    elif review_round == 2:
        return f"""CODE REVIEW — Round {review_round} (Re-review after fixes)
──────────────────────────────────────────
Re-reviewed fixes for BUG-001 and BUG-002.

BUG-001 (Race condition): ✅ FIXED
  Atomic INCR approach is correct. Verified with concurrent test scenario.
  TTL is now set on first INCR — window expiry is reliable.

BUG-002 (Empty 429 body): ✅ FIXED
  Error body is well-structured. Retry-After header present and RFC-compliant.
  request_id included — good for debugging.

NEW FINDING: BUG-003 [P2] Redis failure mode not tested
  The fail-open behaviour on Redis timeout is implemented but has no test.
  This is P2 — not a blocker but should be tracked.

OVERALL: Implementation approved for deployment review.
No P0/P1 issues remaining. BUG-003 logged as tech debt ticket.
"""
    else:
        return f"""CODE REVIEW — Round {review_round}
──────────────────────────────────────────
All previously reported bugs have been addressed.
No new issues found in this round.
Implementation is clean and ready for production.
✅ QA SIGN-OFF
"""


@tool
def report_bug(bug_id: str, description: str, severity: str = "P1") -> str:
    """
    Create a formal bug report to send back to Dev.

    Args:
        bug_id: Bug identifier (e.g. BUG-001).
        description: Detailed description of the bug.
        severity: P0 (blocker), P1 (critical), P2 (major), P3 (minor).
    """
    return f"""BUG REPORT — {bug_id} [{severity}]
──────────────────────────────────────────
{description}

Severity: {severity}
{"⛔ BLOCKER — must fix before any further progress" if severity == "P0" else ""}
{"⚠ CRITICAL — must fix before QA sign-off" if severity == "P1" else ""}
{"↳ Should fix before ship" if severity == "P2" else ""}

Reported by: QA
Status: Open → assigned to Dev
"""


@tool
def sign_off(feature: str, caveats: str = "") -> str:
    """
    Issue QA sign-off for a feature.

    Args:
        feature: The feature being signed off.
        caveats: Any caveats or open minor issues.
    """
    return f"""QA SIGN-OFF — {feature}
──────────────────────────────────────────
✅ All P0 and P1 issues resolved.
✅ Happy path and error paths tested.
✅ Concurrent access tested — no race conditions.
✅ API contract matches spec.

{"Open caveats (non-blocking): " + caveats if caveats else "No open issues."}

This feature is approved from a QA perspective.
Ready for SRE deployment review.
"""


# ---------------------------------------------------------------------------
# SRE tools
# ---------------------------------------------------------------------------

@tool
def assess_deployment_risk(feature: str, implementation_summary: str = "") -> str:
    """
    Assess the deployment risk of a feature.

    Args:
        feature: Feature to assess.
        implementation_summary: Summary of what's being deployed.
    """
    return f"""DEPLOYMENT RISK ASSESSMENT — {feature}
──────────────────────────────────────────
RISK-001 [HIGH] Redis dependency in hot path
  Every API request now requires a Redis call.
  If Redis is unavailable, fail-open behaviour kicks in (requests pass through).
  This is acceptable but needs a runbook and alert.
  Need: confirm Redis cluster SLA and add alert for Redis latency > 20 ms.

RISK-002 [MEDIUM] No DB migration required ✅ (risk mitigated)
  Feature is Redis-only. No schema change. Rollback is feature flag toggle.
  Rollback complexity: LOW — under 2 minutes.

RISK-003 [LOW] Rate limit config in environment variables
  Changing limits requires a redeploy (not runtime configurable).
  Acceptable for v1. Track as tech debt for v2 (config service integration).

CANARY PLAN:
  1% → 10% → 50% → 100% with 15-min bake time.
  Stop criteria: error rate > 1% or p99 latency > 3× baseline.

VERDICT: Acceptable risk profile for production deployment.
Requesting EM go/no-go on RISK-001 (Redis SLA dependency).
"""


@tool
def request_addition(agent: str, what_is_needed: str) -> str:
    """
    Formally request an addition from another agent before deployment can proceed.

    Args:
        agent: The agent who needs to provide the addition.
        what_is_needed: What is specifically needed.
    """
    return f"""SRE REQUEST → {agent.upper()}
──────────────────────────────────────────
Before I can approve this for deployment, I need:

{what_is_needed}

This is a deployment blocker from SRE's perspective.
Please provide the above and hand back to sre_agent for re-assessment.
"""


@tool
def approve_deployment(feature: str) -> str:
    """
    Issue SRE deployment approval for a feature.

    Args:
        feature: The feature being approved.
    """
    return f"""SRE DEPLOYMENT APPROVAL — {feature}
──────────────────────────────────────────
✅ Deployment checklist complete.
✅ Monitoring plan defined (4 golden signals instrumented).
✅ Rollback plan tested — complexity: LOW (feature flag toggle).
✅ Canary strategy documented.
✅ Oncall briefed.

All deployment risks are mitigated or accepted.
Handing to EM for final ship decision.
"""


# ---------------------------------------------------------------------------
# EM tools
# ---------------------------------------------------------------------------

@tool
def arbitrate(conflict_description: str) -> str:
    """
    Arbitrate a conflict or blocker between agents.

    Args:
        conflict_description: Description of what is blocked or in conflict.
    """
    return f"""EM ARBITRATION
──────────────────────────────────────────
Conflict/blocker: {conflict_description[:200]}

Decision:
  I am unblocking this by directing Dev to address the specific concern.
  The ask is reasonable and scoped — it should not require significant rework.

Direction:
  Dev: address the flagged item, then return to the reporting agent for re-review.
  This does not change the scope or timeline of the feature.

If the same issue recurs after Dev's fix, escalate back to me immediately.
"""


@tool
def final_decision(feature: str, decision: str, rationale: str, conditions: str = "") -> str:
    """
    Issue the final ship or hold decision for a feature.

    Args:
        feature: The feature being decided on.
        decision: SHIP or HOLD.
        rationale: Why this decision was made.
        conditions: Any conditions that must be met (for SHIP with caveats).
    """
    icon = "✅" if decision.upper() == "SHIP" else "⛔"
    return f"""ENGINEERING MANAGER FINAL DECISION
══════════════════════════════════════════
Feature : {feature}
Decision: {icon} {decision.upper()}
──────────────────────────────────────────
Rationale:
  {rationale}

{"Conditions before merge:" if conditions else "No conditions — clear to merge."}
{"  " + conditions if conditions else ""}

Stakeholder note:
  The {feature} feature has completed full engineering review (PM spec,
  Dev implementation, QA testing, SRE deployment sign-off) and is
  {'approved for production deployment' if decision.upper() == 'SHIP' else 'on hold pending the conditions listed above'}.

Signed off by: Engineering Manager
══════════════════════════════════════════
"""
