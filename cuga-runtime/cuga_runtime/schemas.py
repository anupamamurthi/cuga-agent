"""
Data models shared across the runtime.

CugaTaskEvent  — what callers send in (the trigger)
CugaResultEvent — what the worker writes back (the outcome)
TaskStatus     — lifecycle states a task can be in
"""

import uuid
import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    QUEUED = "queued"       # accepted, not yet picked up by worker
    RUNNING = "running"     # worker is calling CUGA right now
    COMPLETED = "completed" # CUGA responded successfully
    FAILED = "failed"       # CUGA returned an error or raised an exception
    HITL = "hitl_required"  # CUGA paused waiting for a human decision


class CugaTaskEvent(BaseModel):
    """
    The event a caller submits to kick off a CUGA invocation.

    Required fields:
      query    — the natural-language instruction for CUGA
      persona  — which published CUGA persona/config to use

    Optional fields:
      task_id          — caller-supplied ID (auto-generated if omitted)
      trigger          — human-readable label for what caused this event
                         (e.g. "crm.lead_created", "ticket.escalated")
      context          — any structured data that should be folded into the query
      callback_url     — if set, the runtime will POST the result here when done
      thread_id        — pass an existing CUGA thread_id to continue a conversation
    """

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query: str
    persona: str = "default"
    trigger: str = "manual"
    context: dict[str, Any] = Field(default_factory=dict)
    callback_url: Optional[str] = None
    thread_id: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class CugaResultEvent(BaseModel):
    """
    The outcome written by the worker after CUGA finishes (or fails).
    """

    task_id: str
    status: TaskStatus
    output: Optional[str] = None       # CUGA's final answer text
    error: Optional[str] = None        # error message if status == FAILED
    thread_id: Optional[str] = None    # CUGA thread_id (for follow-up turns)
    completed_at: Optional[str] = None
