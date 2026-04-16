"""
Tests for CugaTaskEvent and CugaResultEvent schemas.

Checks: default field generation, required fields, and round-trip serialisation.
"""

import pytest
from cuga_runtime.schemas import CugaTaskEvent, CugaResultEvent, TaskStatus


class TestCugaTaskEvent:
    def test_minimal_event_has_defaults(self):
        event = CugaTaskEvent(query="Who is the top deal at risk?")
        assert event.task_id is not None
        assert len(event.task_id) > 0
        assert event.persona == "default"
        assert event.trigger == "manual"
        assert event.context == {}
        assert event.callback_url is None
        assert event.created_at is not None

    def test_task_ids_are_unique(self):
        a = CugaTaskEvent(query="task A")
        b = CugaTaskEvent(query="task B")
        assert a.task_id != b.task_id

    def test_caller_supplied_task_id_is_preserved(self):
        event = CugaTaskEvent(task_id="my-custom-id", query="hello")
        assert event.task_id == "my-custom-id"

    def test_context_is_stored(self):
        event = CugaTaskEvent(
            query="Qualify this lead",
            context={"company": "Acme", "deal_size": "$50k"},
        )
        assert event.context["company"] == "Acme"

    def test_round_trip_json(self):
        event = CugaTaskEvent(
            query="Summarise open tickets",
            persona="support_agent",
            trigger="ticket.escalated",
            context={"ticket_id": "T-999"},
        )
        restored = CugaTaskEvent.model_validate_json(event.model_dump_json())
        assert restored.task_id == event.task_id
        assert restored.query == event.query
        assert restored.context == event.context


class TestCugaResultEvent:
    def test_queued_result(self):
        result = CugaResultEvent(task_id="abc", status=TaskStatus.QUEUED)
        assert result.output is None
        assert result.error is None
        assert result.completed_at is None

    def test_completed_result(self):
        result = CugaResultEvent(
            task_id="abc",
            status=TaskStatus.COMPLETED,
            output="Lead scored 8/10 — recommend immediate follow-up.",
            thread_id="thread-123",
            completed_at="2026-04-14T10:00:00",
        )
        assert result.status == TaskStatus.COMPLETED
        assert "8/10" in result.output

    def test_failed_result(self):
        result = CugaResultEvent(
            task_id="abc",
            status=TaskStatus.FAILED,
            error="CUGA returned HTTP 503",
        )
        assert result.status == TaskStatus.FAILED
        assert result.output is None
