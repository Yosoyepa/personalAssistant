"""Trace event contracts and local recorder."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TraceEventType(str, Enum):
    agent_started = "agent.started"
    context_selected = "context.selected"
    llm_called = "llm.called"
    tool_called = "tool.called"
    guardrail_checked = "guardrail.checked"
    approval_requested = "approval.requested"
    agent_completed = "agent.completed"
    agent_failed = "agent.failed"


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str = Field(min_length=1)
    event_type: TraceEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str = Field(min_length=1)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    context_refs: list[str] = Field(default_factory=list)
    tool_call: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    output_summary: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] = Field(default_factory=dict)
    parent_event_id: str | None = None


class TraceRecorder:
    """In-memory trace recorder for local development."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self._events.append(event)

    def list_for_tenant(self, tenant_id: str) -> list[TraceEvent]:
        return [event for event in self._events if event.tenant_id == tenant_id]

    def list_for_run(self, tenant_id: str, run_id: str) -> list[TraceEvent]:
        return [event for event in self._events if event.tenant_id == tenant_id and event.run_id == run_id]
