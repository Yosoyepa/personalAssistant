"""Trace event application DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from personal_assistant.domain.common.privacy import (
    redact_trace_mapping,
    safe_category,
    safe_context_refs,
    safe_identifier,
    safe_optional_identifier,
    safe_trace_run_id,
)


class TraceEventType(str, Enum):
    agent_started = "agent.started"
    context_selected = "context.selected"
    llm_called = "llm.called"
    tool_called = "tool.called"
    guardrail_checked = "guardrail.checked"
    approval_requested = "approval.requested"
    agent_completed = "agent.completed"
    agent_failed = "agent.failed"


#: Fields that must be present and non-empty for each event type before the
#: event may be persisted. Grounded in what the application legitimately
#: emits today; enforced fail-closed at write time by every trace recorder.
REQUIRED_TRACE_FIELDS: dict[TraceEventType, tuple[str, ...]] = {
    TraceEventType.agent_started: ("input_summary",),
    TraceEventType.context_selected: ("context_refs",),
    TraceEventType.llm_called: ("model",),
    TraceEventType.tool_called: ("tool_call",),
    TraceEventType.guardrail_checked: ("validation",),
    TraceEventType.approval_requested: ("tool_call",),
    TraceEventType.agent_completed: ("output_summary",),
    TraceEventType.agent_failed: ("error",),
}


class IncompleteTraceEventError(ValueError):
    """A trace event is missing fields required by its event type."""


class TraceEvent(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_assignment=True
    )

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str = Field(min_length=1)
    event_type: TraceEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str = Field(min_length=1)
    input_summary: dict[str, Any] = Field(default_factory=dict, repr=False)
    context_refs: list[str] = Field(default_factory=list)
    tool_call: dict[str, Any] = Field(default_factory=dict, repr=False)
    model: str | None = None
    output_summary: dict[str, Any] = Field(default_factory=dict, repr=False)
    validation: dict[str, Any] = Field(default_factory=dict, repr=False)
    error: dict[str, Any] = Field(default_factory=dict, repr=False)
    parent_event_id: str | None = None

    @field_validator("trace_id", "agent_id", "tenant_id", mode="before")
    @classmethod
    def _validate_identifiers(cls, value: object) -> str:
        return safe_identifier(value)

    @field_validator("run_id", mode="before")
    @classmethod
    def _validate_run_id(cls, value: object) -> str:
        return safe_trace_run_id(value)

    @field_validator("parent_event_id", mode="before")
    @classmethod
    def _validate_optional_identifier(cls, value: object | None) -> str | None:
        return safe_optional_identifier(value)

    @field_validator("model", mode="before")
    @classmethod
    def _validate_model(cls, value: object | None) -> str | None:
        return safe_category(value)

    @field_validator("context_refs", mode="before")
    @classmethod
    def _validate_context_refs(cls, value: object) -> list[str]:
        return safe_context_refs(value)

    @field_validator(
        "input_summary",
        "tool_call",
        "output_summary",
        "validation",
        "error",
        mode="before",
    )
    @classmethod
    def _validate_metadata(cls, value: object) -> dict[str, Any]:
        return redact_trace_mapping(value)

    @field_serializer("trace_id", "agent_id", "tenant_id")
    def _serialize_identifiers(self, value: str) -> str:
        return safe_identifier(value)

    @field_serializer("run_id")
    def _serialize_run_id(self, value: str) -> str:
        return safe_trace_run_id(value)

    @field_serializer("parent_event_id")
    def _serialize_optional_identifier(self, value: str | None) -> str | None:
        return safe_optional_identifier(value)

    @field_serializer("model")
    def _serialize_model(self, value: str | None) -> str | None:
        return safe_category(value)

    @field_serializer("context_refs")
    def _serialize_context_refs(self, value: list[str]) -> list[str]:
        return safe_context_refs(value)

    @field_serializer(
        "input_summary", "tool_call", "output_summary", "validation", "error"
    )
    def _serialize_metadata(self, value: dict[str, Any]) -> dict[str, Any]:
        return redact_trace_mapping(value)

    def for_persistence(self) -> "TraceEvent":
        """Materialize a privacy-safe copy even after mutable-field changes."""

        return type(self).model_validate(self.model_dump(mode="python"))


def require_trace_completeness(event: TraceEvent) -> TraceEvent:
    """Fail closed when an event lacks a field its event type requires.

    Shared by every trace recorder so an incomplete event raises a clear
    error at write time and a partial row is never persisted. The event is
    validated as emitted, before privacy redaction may legitimately strip
    non-allowlisted payload keys. The message only names static
    identifiers (event type and field names), never event payloads.
    """

    missing = tuple(
        field_name
        for field_name in REQUIRED_TRACE_FIELDS[event.event_type]
        if not getattr(event, field_name)
    )
    if missing:
        required = ", ".join(missing)
        raise IncompleteTraceEventError(
            f"incomplete trace event: {event.event_type.value} requires {required}"
        )
    return event
