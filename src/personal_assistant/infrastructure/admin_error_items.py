"""Normalized error rows across trace, workflow, and outbox sources."""

from __future__ import annotations

from typing import Any

from personal_assistant.application.dto.events import OutboxMessage
from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.application.dto.workflows import WorkflowState
from personal_assistant.infrastructure.admin_items import (
    _outbox_item,
    _trace_item,
    _workflow_state_item,
)
from personal_assistant.infrastructure.admin_redaction import (
    _redacted_failure_message,
)
from personal_assistant.infrastructure.admin_time import _iso
from personal_assistant.infrastructure.admin_trace_categories import (
    _trace_error_category,
)
from personal_assistant.infrastructure.admin_trace_filters import (
    _trace_error_message,
    _trace_error_operation,
    _trace_error_type,
)


def _trace_error_item(event: TraceEvent) -> dict[str, Any]:
    error_type = _trace_error_type(event)
    return {
        "timestamp": _iso(event.timestamp),
        "source": "trace",
        "category": _trace_error_category(event),
        "event_type": event.event_type.value,
        "operation": _trace_error_operation(event),
        "type": error_type,
        "error_type": error_type,
        "message": _trace_error_message(event),
        "run_id": event.run_id,
        "workflow_id": "",
        "agent_id": event.agent_id,
        "details": _trace_item(event),
    }


def _workflow_error_item(state: WorkflowState) -> dict[str, Any]:
    return {
        "timestamp": _iso(state.updated_at),
        "source": "workflow",
        "category": "workflow",
        "event_type": state.workflow_type,
        "operation": state.step,
        "type": state.workflow_type,
        "message": _redacted_failure_message(state.data, fallback=state.step),
        "run_id": "",
        "workflow_id": state.workflow_id,
        "agent_id": "",
        "details": _workflow_state_item(state),
    }


def _outbox_error_item(message: OutboxMessage) -> dict[str, Any]:
    return {
        "timestamp": _iso(message.next_attempt_at or message.created_at),
        "source": "outbox",
        "category": "tool",
        "event_type": message.event.type,
        "operation": message.event.type,
        "type": message.event.type,
        "message": _redacted_failure_message(message.event.data),
        "run_id": "",
        "workflow_id": "",
        "agent_id": "",
        "details": _outbox_item(message),
    }
