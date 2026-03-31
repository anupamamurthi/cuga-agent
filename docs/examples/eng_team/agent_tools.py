"""
Simulated tools for the Engineering Team multi-agent demo.

Each function is a @tool that a specific agent uses.  The responses are
structured so that downstream agents can build on them.
"""
from __future__ import annotations

from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# PM tools
# ---------------------------------------------------------------------------

@tool
def create_user_story(feature_request: str, target_persona: str = "developer") -> str:
    """
    Format a raw feature request into a structured user story.

    Args:
        feature_request: The raw feature request from the user.
        target_persona: The primary user persona for this feature.
    """
    return f"""USER STORY
──────────────────────────────────────────
As a {target_persona},
I want {feature_request},
So that I can achieve my goals more reliably and efficiently.

Context:
  This feature was requested to improve the system's capabilities
  in handling {feature_request.lower()}.

Labels: feature, needs-estimation
Status: Draft
"""


@tool
def define_acceptance_criteria(feature: str, constraints: str = "none") -> str:
    """
    Generate structured acceptance criteria for a feature using Given/When/Then.

    Args:
        feature: The feature being specified.
        constraints: Any known technical or business constraints.
    """
    return f"""ACCEPTANCE CRITERIA — {feature}
──────────────────────────────────────────
AC-001 (Happy path)
  Given the system is operational and the user is authenticated
  When the user invokes {feature}
  Then the system responds successfully within 200 ms at p99

AC-002 (Invalid input)
  Given the user submits a malformed or missing required parameter
  When the request is processed
  Then the system returns HTTP 422 with a descriptive error message

AC-003 (Authorisation)
  Given the user lacks the required permission
  When they attempt to use {feature}
  Then the system returns HTTP 403 and logs the attempt

AC-004 (Rate / volume)
  Given {constraints if constraints != 'none' else 'standard load conditions'}
  When the feature operates at peak traffic
  Then no degradation beyond 10% of baseline latency is observed

AC-005 (Idempotency)
  Given the same request is sent twice
  When the system processes both
  Then the result is identical and no duplicate side-effects occur
"""


@tool
def estimate_story_points(complexity_description: str) -> str:
    """
    Estimate story points (Fibonacci) based on a plain-English complexity description.

    Args:
        complexity_description: Description of the work and its complexity.
    """
    # Simple heuristic-based response
    desc_lower = complexity_description.lower()
    if any(w in desc_lower for w in ["simple", "trivial", "minor", "config"]):
        points, rationale = 2, "Low complexity — config or single-component change."
    elif any(w in desc_lower for w in ["moderate", "medium", "standard", "typical"]):
        points, rationale = 5, "Medium complexity — touches 2-3 components, needs tests."
    elif any(w in desc_lower for w in ["complex", "cross-service", "distributed", "migration"]):
        points, rationale = 13, "High complexity — cross-service or schema change involved."
    else:
        points, rationale = 8, "Above-average complexity — multiple unknowns remain."

    return f"""STORY POINT ESTIMATE
──────────────────────────────────────────
Estimate : {points} points
Rationale: {rationale}
Sprint fit: {'Fits in one sprint' if points <= 8 else 'Consider splitting into 2 stories'}
"""


# ---------------------------------------------------------------------------
# Dev tools
# ---------------------------------------------------------------------------

@tool
def design_api_endpoint(
    endpoint_name: str,
    http_method: str = "POST",
    description: str = "",
) -> str:
    """
    Design a REST API endpoint with request/response schema and error codes.

    Args:
        endpoint_name: Name or path of the endpoint (e.g. 'rate-limits').
        http_method: HTTP verb — GET, POST, PUT, PATCH, DELETE.
        description: What the endpoint does.
    """
    method = http_method.upper()
    path = endpoint_name.lower().replace(" ", "-")
    return f"""API ENDPOINT DESIGN — {method} /v1/{path}
──────────────────────────────────────────
Description : {description or f'Manages {endpoint_name}'}
Auth        : Bearer token required (JWT)

Request:
  Headers:
    Authorization: Bearer <token>
    Content-Type : application/json

  Body (JSON):
    {{
      "resource_id": "string (required)",
      "config":      {{ ... }}  // feature-specific config
    }}

Response 200/201:
  {{
    "id":         "uuid",
    "status":     "active",
    "created_at": "ISO-8601",
    "config":     {{ ... }}
  }}

Error codes:
  400 Bad Request   — missing or invalid fields
  401 Unauthorized  — missing/expired token
  403 Forbidden     — insufficient permissions
  409 Conflict      — resource already exists
  422 Unprocessable — validation failed
  429 Too Many Reqs — caller is rate-limited
  500 Internal      — unexpected server error

Rate-limit headers returned on every response:
  X-RateLimit-Limit     : <max>
  X-RateLimit-Remaining : <remaining>
  X-RateLimit-Reset     : <unix-timestamp>
"""


@tool
def suggest_implementation_approach(feature: str, tech_stack: str = "Python/FastAPI") -> str:
    """
    Suggest an implementation approach with design patterns and key components.

    Args:
        feature: The feature to implement.
        tech_stack: The technology stack in use.
    """
    return f"""IMPLEMENTATION APPROACH — {feature}
──────────────────────────────────────────
Stack   : {tech_stack}
Pattern : Middleware / Chain-of-Responsibility

Key components:
  1. RateLimitMiddleware   — intercepts every request pre-handler
  2. RateLimitStore        — Redis-backed sliding-window counter
  3. RateLimitConfig       — per-route / per-client configurable limits
  4. RateLimitExceededError — raised when limit hit; mapped to HTTP 429

Integration points:
  - Register middleware in app startup (before auth middleware)
  - Config loaded from environment / feature-flag service
  - Store uses existing Redis cluster (no new infra needed)

Migration path:
  - Phase 1: deploy with limits set to 10× real usage (observe only)
  - Phase 2: tighten limits gradually over 2 weeks
  - Phase 3: enforce hard limits with clear error messages

Estimated files changed: 4 new, 2 modified
Blast radius: all API routes (read-only middleware — no logic change)
"""


@tool
def check_existing_patterns(pattern_type: str) -> str:
    """
    Check what similar patterns already exist in the codebase to avoid duplication.

    Args:
        pattern_type: The type of pattern to look for (e.g. 'middleware', 'auth', 'caching').
    """
    return f"""EXISTING PATTERN AUDIT — {pattern_type}
──────────────────────────────────────────
Found:
  - auth_middleware.py       : JWT validation, good reference for middleware structure
  - cache_store.py           : Redis wrapper — reuse for rate-limit store
  - error_handlers.py        : Centralised HTTP exception mapping — extend for 429

Reuse recommendation:
  ✓ Extend CacheStore for sliding-window counters
  ✓ Follow auth_middleware.py's pattern for request interception
  ✓ Register new RateLimitExceededError in error_handlers.py
  ✗ Do NOT add rate-limit logic inside individual route handlers

No duplication risk if the above guidelines are followed.
"""


# ---------------------------------------------------------------------------
# QA tools
# ---------------------------------------------------------------------------

@tool
def generate_test_plan(feature: str, acceptance_criteria: str = "") -> str:
    """
    Generate a structured test plan covering unit, integration, and E2E tests.

    Args:
        feature: The feature being tested.
        acceptance_criteria: The ACs to map tests against.
    """
    return f"""TEST PLAN — {feature}
──────────────────────────────────────────
UNIT TESTS (target: 85% line coverage)
  TC-001 [P0] test_happy_path_returns_success
  TC-002 [P0] test_invalid_input_returns_422
  TC-003 [P0] test_unauthorised_returns_403
  TC-004 [P1] test_idempotent_duplicate_request
  TC-005 [P1] test_config_boundary_values (0, max-1, max, max+1)

INTEGRATION TESTS (all P0 ACs must pass)
  TC-006 [P0] test_end_to_end_with_real_store
  TC-007 [P0] test_redis_connection_failure_graceful_degradation
  TC-008 [P0] test_concurrent_requests_at_limit_boundary
  TC-009 [P1] test_rate_limit_headers_present_on_every_response

E2E TESTS (happy path + 2 error paths)
  TC-010 [P0] test_full_flow_authenticated_user
  TC-011 [P0] test_full_flow_rate_limit_exceeded_and_retry_after
  TC-012 [P1] test_full_flow_unauthorised_user_blocked

PERFORMANCE TESTS
  TC-013 [P0] load_test_at_2x_peak — p99 must remain < 200 ms

{'Notes from ACs: ' + acceptance_criteria[:200] if acceptance_criteria else ''}
"""


@tool
def identify_edge_cases(feature: str) -> str:
    """
    Identify edge cases and risk areas for a given feature.

    Args:
        feature: The feature to analyse for edge cases.
    """
    return f"""EDGE CASE ANALYSIS — {feature}
──────────────────────────────────────────
HIGH RISK
  ⚠ Race condition at limit boundary
      Two requests arrive simultaneously when counter = max-1
      Both could read max-1, both increment, both succeed → limit violated
      Mitigation: use Redis INCR + atomic compare, not read-then-write

  ⚠ Counter not reset after window expires
      If Redis key TTL drifts (clock skew, GC pause), window never resets
      Mitigation: use server-side TTL with explicit reset on window boundary

MEDIUM RISK
  ↳ Client sends requests out of order (retry storms)
  ↳ Token expiry exactly when rate-limit check occurs
  ↳ Config reload mid-request changes the limit value

LOW RISK
  ↳ Very large client IDs / API keys (buffer overflow check)
  ↳ Requests with missing Authorization header bypass rate-limit check
  ↳ IPv6 client addresses not normalised before key lookup

SECURITY
  ⚠ Header spoofing: client sets X-RateLimit-Remaining: 9999 themselves
      Ensure headers are only set server-side, never trusted from client
"""


@tool
def assess_test_coverage(feature: str, test_count: int = 12) -> str:
    """
    Assess whether the test coverage is sufficient to ship the feature.

    Args:
        feature: The feature being evaluated.
        test_count: Number of test cases written.
    """
    coverage_pct = min(95, 60 + test_count * 3)
    ready = coverage_pct >= 80 and test_count >= 10
    return f"""COVERAGE ASSESSMENT — {feature}
──────────────────────────────────────────
Tests written     : {test_count}
Estimated coverage: ~{coverage_pct}% line coverage
P0 ACs covered    : {'All 5' if test_count >= 8 else 'Partial — add more P0 tests'}
Branch coverage   : {'Good' if test_count >= 10 else 'Needs improvement'}

Gaps identified:
  {'None — coverage looks solid' if ready else '- Need more negative / error path tests'}

Recommendation: {'✅ READY TO SHIP from QA perspective' if ready else '⛔ NOT READY — add tests before merging'}

Uncovered risk areas:
  - Redis failure fallback path (graceful degradation)
  - Clock skew / TTL drift scenario
"""


# ---------------------------------------------------------------------------
# SRE tools
# ---------------------------------------------------------------------------

@tool
def generate_deployment_checklist(service: str, change_type: str = "feature") -> str:
    """
    Generate a production deployment checklist for a service change.

    Args:
        service: The service being deployed.
        change_type: Type of change — feature, bugfix, migration, hotfix.
    """
    return f"""DEPLOYMENT CHECKLIST — {service} ({change_type})
──────────────────────────────────────────
PRE-DEPLOY
  [ ] Feature flag created: feature_{service.lower().replace('-','_')}_enabled (default: OFF)
  [ ] DB migration tested on staging with production data snapshot
  [ ] Config values added to vault: RATE_LIMIT_WINDOW_SECONDS, RATE_LIMIT_MAX_REQUESTS
  [ ] Load test completed at 2× peak (results attached to PR)
  [ ] Oncall briefed — knows the rollback trigger criteria
  [ ] Changelog entry written

DEPLOY SEQUENCE
  [ ] 1% canary — observe for 15 min
  [ ] 10% canary — observe for 15 min
  [ ] 50% — observe for 30 min
  [ ] 100% — confirm dashboards green

POST-DEPLOY
  [ ] Dashboard {service}-rate-limiting-overview showing expected signal
  [ ] Synthetic monitor firing correctly on /health
  [ ] Alert fired and resolved in test (confirm pagerduty routing)
  [ ] Runbook updated with new operational notes
  [ ] Feature flag flipped ON after 24 h of clean operation

Estimated deploy duration: 2 hours (including bake time)
"""


@tool
def suggest_monitoring(feature: str) -> str:
    """
    Suggest monitoring setup (metrics, alerts, dashboards) for a new feature.

    Args:
        feature: The feature to instrument.
    """
    return f"""MONITORING PLAN — {feature}
──────────────────────────────────────────
METRICS TO EMIT
  rate_limit.requests_total        [counter]  — all requests checked
  rate_limit.requests_allowed      [counter]  — passed through
  rate_limit.requests_rejected     [counter]  — hit limit (→ 429)
  rate_limit.store_latency_ms      [histogram] — Redis call latency
  rate_limit.window_resets_total   [counter]  — window expirations

ALERTS
  CRITICAL: rate_limit.requests_rejected / rate_limit.requests_total > 20% for 5 min
    → Page oncall: possible misconfiguration or abuse spike
  WARNING : rate_limit.store_latency_ms p99 > 50 ms for 5 min
    → Slack #sre-alerts: Redis latency degrading

DASHBOARD
  Name: {feature.lower().replace(' ','-')}-overview
  Panels:
    - Requests/s (allowed vs rejected, stacked)
    - Rejection rate % (with 1% / 5% / 20% threshold lines)
    - Store latency p50/p95/p99
    - Active windows by client (top 10 table)

SLO RECOMMENDATION
  Availability: 99.9% (allowing max 43 min/month downtime)
  Latency SLO : p99 < 50 ms for rate-limit store calls
"""


@tool
def classify_rollback_complexity(change_description: str) -> str:
    """
    Classify the rollback complexity and produce a rollback plan.

    Args:
        change_description: Description of the change being deployed.
    """
    desc_lower = change_description.lower()
    if "migration" in desc_lower or "schema" in desc_lower:
        level, time, steps = "HIGH", "< 1 hour", [
            "1. Disable feature flag immediately (< 2 min)",
            "2. Redeploy previous image tag",
            "3. Run schema down-migration (pre-tested): `alembic downgrade -1`",
            "4. Verify application starts cleanly against reverted schema",
            "5. Notify stakeholders within 10 min",
        ]
    elif "config" in desc_lower or "flag" in desc_lower:
        level, time, steps = "LOW", "< 2 min", [
            "1. Flip feature flag to OFF in config service",
            "2. Confirm 429 response no longer returned (run smoke test)",
            "3. Monitor error rate for 5 min post-rollback",
        ]
    else:
        level, time, steps = "MEDIUM", "< 15 min", [
            "1. Disable feature flag (< 2 min)",
            "2. Redeploy previous image: `kubectl rollout undo deployment/{change_description[:20].lower().replace(' ','-')}`",
            "3. Confirm pods healthy: `kubectl rollout status`",
            "4. Run smoke tests against production",
            "5. Notify stakeholders",
        ]

    return f"""ROLLBACK PLAN
──────────────────────────────────────────
Complexity : {level}
Target RTO : {time}

Steps:
{''.join(chr(10) + '  ' + s for s in steps)}

Trigger criteria (any one → rollback):
  - Error rate > 1% sustained 5 min
  - p99 latency > 3× pre-deploy baseline
  - On-call engineer judgment

Owner: Oncall SRE ({level} rollback — escalate to DBA if schema involved)
"""
