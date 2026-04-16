"""
A minimal fake CUGA server for e2e testing.

Mimics exactly what CUGA's POST /stream endpoint does:
  - Accepts {"query": "..."}
  - Streams SSE events ending with event: Answer

This lets the e2e test run the full HTTP path — runtime → HTTP → fake CUGA →
HTTP ← runtime — without needing a real CUGA installation.

The server is configurable so tests can inject specific answers, simulate
delays, or trigger errors.
"""

import asyncio
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

# ── Shared state ──────────────────────────────────────────────────────────────
# Tests set these before submitting events to control what the fake returns.

_answer: str = "Default fake CUGA answer."
_delay_seconds: float = 0.0
_force_error: bool = False
_received_queries: list[dict] = []   # lets tests inspect what was sent


def configure(
    answer: str = "Default fake CUGA answer.",
    delay: float = 0.0,
    force_error: bool = False,
) -> None:
    """Call from test setup to control the fake server's behaviour."""
    global _answer, _delay_seconds, _force_error, _received_queries
    _answer = answer
    _delay_seconds = delay
    _force_error = force_error
    _received_queries = []


def get_received_queries() -> list[dict]:
    return list(_received_queries)


# ── FastAPI app ───────────────────────────────────────────────────────────────

fake_cuga = FastAPI(title="Fake CUGA Server")


@fake_cuga.post("/stream")
async def stream(request: Request):
    """Fake /stream that returns SSE like CUGA does."""
    body = await request.json()
    thread_id = request.headers.get("X-Thread-ID", "")
    _received_queries.append({"query": body.get("query", ""), "thread_id": thread_id})

    async def event_gen():
        if _delay_seconds > 0:
            await asyncio.sleep(_delay_seconds)

        if _force_error:
            yield "event: Error\ndata: Fake CUGA server error\n\n"
            return

        # Emit one intermediate event (like real CUGA does during reasoning)
        yield "event: reasoning\ndata: Analysing the situation...\n\n"
        await asyncio.sleep(0.01)

        # Final answer
        yield f"event: Answer\ndata: {_answer}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@fake_cuga.get("/health")
async def health():
    return {"status": "ok", "server": "fake-cuga"}
