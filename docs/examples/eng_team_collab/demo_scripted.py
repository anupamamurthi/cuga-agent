#!/usr/bin/env python3
"""
demo_scripted.py — Hardcoded multi-handoff walkthrough
========================================================

Shows exactly how the routing loop works with a realistic conversation
that contains 4 handback moments:

  PM → Dev → QA (bug!) → Dev → QA (2nd bug!) → Dev → QA (ok) → SRE → EM → COMPLETE

No LLM calls — pure routing logic demo.
Run with:  uv run python demo_scripted.py
"""
from __future__ import annotations

import asyncio
from orchestrator import CollabOrchestrator, Turn

FEATURE = "Add JWT token refresh endpoint"

# Each entry: (agent, message, handoff_to)
# This is what the LLM *would* say — hardcoded so you can see all handoffs.
SCRIPT: list[tuple[str, str, str]] = [

    ("pm_agent", """\
USER STORY
──────────────────────────────────────────
As an API client, I want to exchange an expired access token for a new one
using a refresh token, so that I don't have to re-authenticate on every expiry.

Acceptance Criteria:
  AC-001  POST /v1/auth/refresh accepts { refresh_token: string }
  AC-002  Returns { access_token, expires_in, token_type } on success
  AC-003  Returns 401 if refresh token is expired or invalid
  AC-004  Refresh token is rotated on every use (old one invalidated)
  AC-005  Rate limited: max 10 refresh calls per minute per client
  AC-006  All tokens are JWT signed with RS256

Out of scope: SSO / federated identity (v2)
""", "dev_agent"),

    ("dev_agent", """\
IMPLEMENTATION DESIGN v1 — JWT Refresh Endpoint
──────────────────────────────────────────
POST /v1/auth/refresh

Components:
  RefreshHandler        — validates incoming refresh token, issues new pair
  TokenStore (Redis)    — maps refresh_token → user_id + expiry
  JWTFactory            — signs access tokens with RS256 private key
  RateLimiter           — 10 req/min per client_id (sliding window)

Key decisions:
  • Refresh token is opaque (UUID) — not a JWT, stored in Redis
  • Access token is JWT (15 min TTL), refresh token is 30 days
  • Token rotation: delete old refresh token before issuing new one
  • Atomic Redis operation: GETDEL to read-and-delete in one call

On success:  200 { access_token, refresh_token, expires_in, token_type: "Bearer" }
On failure:  401 { code: "invalid_refresh_token", message: "...", request_id }
""", "qa_agent"),

    # --- QA ROUND 1: finds 2 bugs ---
    ("qa_agent", """\
CODE REVIEW — Round 1
──────────────────────────────────────────
Reviewed: JWT refresh endpoint implementation v1.

BUGS FOUND: 2

BUG-001 [P0] Race condition in token rotation
  Description: Implementation deletes old token then inserts new one as two
    separate Redis commands. If the process crashes between the two ops,
    the old token is gone but no new token was issued — user is locked out.
  Reproduction: kill the process between GETDEL and SET.
  Fix needed: use a Redis transaction (MULTI/EXEC) or Lua script to make
    the delete+insert atomic.

BUG-002 [P1] Refresh token not included in 200 response
  Description: The response schema shows access_token but is missing
    refresh_token. Client has no way to get the new rotated token.
  Fix needed: include new refresh_token in the 200 response body.

PASSED:
  ✓ Rate limiting wired correctly
  ✓ 401 error body is well-structured
  ✓ RS256 signing confirmed
""", "dev_agent"),

    # --- DEV ROUND 2: fixes BUG-001 and BUG-002 ---
    ("dev_agent", """\
BUG FIXES — v2
──────────────────────────────────────────
Addressing BUG-001 and BUG-002 from QA review.

BUG-001 FIX (Race condition):
  Before: GETDEL(old_token), SET(new_token)  ← two separate ops
  After:  Lua script (atomic):
    local old = redis.call('GETDEL', KEYS[1])
    if old then
      redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
      return old
    end
    return nil
  This is a single atomic operation — no window for partial failure.
  If the script fails, the old token remains valid. User can retry.

BUG-002 FIX (Missing refresh_token in response):
  Added new_refresh_token to 200 response:
  { access_token, refresh_token, expires_in, token_type: "Bearer" }
  Old refresh_token is invalidated server-side before responding.

No other changes. Sending back to QA for re-review.
""", "qa_agent"),

    # --- QA ROUND 2: BUG-001 fixed, but finds a NEW bug ---
    ("qa_agent", """\
CODE REVIEW — Round 2 (Re-review after fixes)
──────────────────────────────────────────
BUG-001 (Race condition): ✅ FIXED — Lua script is correct and atomic.
BUG-002 (Missing refresh_token): ✅ FIXED — new token present in response.

NEW FINDING: BUG-003 [P0] Refresh token expiry not validated
  Description: The Lua script checks that the old token EXISTS in Redis,
    but does not check whether it has expired. Redis TTL expiry is passive
    (key disappears), but if TTL is not set correctly, an expired token
    could be accepted indefinitely.
  Reproduction: set refresh token TTL to 0 (no expiry) — token never expires.
  Fix needed: confirm TTL is set to 30 days (2592000 seconds) on every
    new token write, and add an explicit expiry check in the Lua script.
""", "dev_agent"),

    # --- DEV ROUND 3: fixes BUG-003 ---
    ("dev_agent", """\
BUG FIX — v3
──────────────────────────────────────────
Addressing BUG-003: Refresh token expiry not validated.

Root cause: TTL was set via application-level config but the Lua script
  did not enforce it explicitly, meaning a misconfigured TTL=0 would silently
  create immortal tokens.

Fix:
  1. Lua script now reads the TTL of the key before accepting it:
       local ttl = redis.call('TTL', KEYS[1])
       if ttl == -1 or ttl == -2 then return nil end  -- no TTL or missing → reject
  2. All new refresh token writes now call SET with EX 2592000 (30 days) explicitly.
  3. Added integration test: token with no TTL is rejected with 401.

Three bugs found, three fixed. No regressions introduced.
""", "qa_agent"),

    # --- QA ROUND 3: clean ---
    ("qa_agent", """\
CODE REVIEW — Round 3 (Final re-review)
──────────────────────────────────────────
BUG-003 (Expiry not validated): ✅ FIXED
  Lua script TTL check is correct. Tokens with no TTL are rejected.
  Integration test covers this case.

All P0 and P1 issues resolved across 3 review rounds.
No new issues found.

✅ QA SIGN-OFF
Ready for SRE deployment review.
""", "sre_agent"),

    # --- SRE: flags one risk, needs EM decision ---
    ("sre_agent", """\
DEPLOYMENT RISK ASSESSMENT — JWT Refresh Endpoint
──────────────────────────────────────────
RISK-001 [MEDIUM] Redis as the sole source of truth for session validity
  If Redis is unavailable, ALL token refresh attempts fail.
  Current implementation is fail-closed (returns 503 on Redis timeout).
  This is safer than fail-open but means an outage = all users must re-login.
  Mitigation available: short-lived local cache (30s) to absorb Redis blips.
  Decision needed from EM: accept fail-closed, or implement local cache?

RISK-002 [LOW] RS256 private key rotation not in scope
  Key rotation will be needed eventually. Not a blocker for v1.
  Logged as tech debt.

RISK-003 [RESOLVED] Token rotation atomicity ← fixed by Dev in v3.

Canary plan: 1% → 10% → 50% → 100% with 15-min bake time.
Rollback: feature flag toggle, < 2 min.

Requesting EM go/no-go on RISK-001 before I issue deployment approval.
""", "em_agent"),

    # --- EM: makes the call ---
    ("em_agent", """\
EM DECISION ON RISK-001
──────────────────────────────────────────
Decision: Accept fail-closed for v1. Do NOT add local cache now.

Rationale:
  A 30s local cache would allow revoked tokens to remain valid during
  a Redis outage — that's a security regression we can't accept at launch.
  Fail-closed (503 on Redis outage) is the correct default for an auth endpoint.
  Redis HA (replication + sentinel) is already in place — outage risk is low.

RISK-001 accepted. SRE approved to proceed with deployment.

DECISION: SHIP ✅
──────────────────────────────────────────
Rationale:
  Feature completed 3 QA review rounds, all P0/P1 bugs fixed.
  SRE risks assessed and mitigated or consciously accepted.
  Atomic token rotation, expiry validation, and rate limiting all confirmed.

Conditions: none — clear to merge.

Stakeholder note:
  The JWT token refresh endpoint is ready for production. Users will no
  longer need to re-authenticate when their access token expires.
""", "COMPLETE"),

]


async def run():
    print(f"\n{'═'*66}")
    print(f"  Scripted Multi-Handoff Demo — {FEATURE}")
    print(f"  {len(SCRIPT)} turns · 4 handbacks demonstrated")
    print(f"{'═'*66}\n")

    AGENT_LABELS = {
        "pm_agent":  "📋 PM ",
        "dev_agent": "💻 Dev",
        "qa_agent":  "🧪 QA ",
        "sre_agent": "🔧 SRE",
        "em_agent":  "🎯 EM ",
    }

    HANDBACK_MOMENTS = {
        # (from, to): description
        ("qa_agent",  "dev_agent"): "← BUG FOUND — bouncing back to Dev",
        ("sre_agent", "em_agent"):  "← RISK DECISION — escalating to EM",
    }

    prev_agent = None
    for i, (agent, message, handoff_to) in enumerate(SCRIPT, start=1):
        label      = AGENT_LABELS.get(agent, agent)
        next_label = AGENT_LABELS.get(handoff_to, handoff_to)

        # Print handback banner when routing goes "backwards"
        key = (agent, handoff_to)
        if key in HANDBACK_MOMENTS:
            print(f"\n  {'⚡'*3}  {HANDBACK_MOMENTS[key]}  {'⚡'*3}")

        print(f"\n  {'─'*66}")
        print(f"  Round {i:02d}  {label}")
        print(f"  {'─'*66}")
        for line in message.strip().splitlines():
            print(f"    {line}")

        if handoff_to == "COMPLETE":
            print(f"\n  → ✅ COMPLETE")
        else:
            print(f"\n  → handoff to {next_label}")

        await asyncio.sleep(0.05)   # tiny pause so output is readable when piped

    print(f"\n{'═'*66}")
    print(f"  Handback summary:")
    print(f"    Round 3  🧪 QA  → 💻 Dev  (BUG-001 + BUG-002 found)")
    print(f"    Round 5  🧪 QA  → 💻 Dev  (BUG-003 found — new bug on re-review)")
    print(f"    Round 7  🧪 QA  → 💻 Dev  ← 3rd review, all clear")
    print(f"    Round 8  🔧 SRE → 🎯 EM   (RISK-001 needs go/no-go)")
    print(f"  Total rounds: {len(SCRIPT)}  ·  Pipeline: SHIP ✅")
    print(f"{'═'*66}\n")


if __name__ == "__main__":
    asyncio.run(run())
