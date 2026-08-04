"""Trace completeness evals against the real recorder write path.

The reminder-workflow scenario runs the production workflow hermetically
(in-memory ports, no LLM) and reports exactly which event types were
emitted and which contract-required fields were present, so a trace
completeness regression changes the output and fails the case. The
incomplete-event scenario proves the write path fails closed: a complete
control event is accepted while the same event missing one required field
is rejected and never persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.in_memory_uow import (
    InMemoryReminderUnitOfWork,
)
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.tracing import (
    REQUIRED_TRACE_FIELDS,
    IncompleteTraceEventError,
    TraceEvent,
    TraceEventType,
    require_trace_completeness,
)
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.evals.schema import StrictModel

NOW = datetime(2026, 6, 20, 12, tzinfo=UTC)
TENANT = "tenant-trace-eval"

#: Minimal privacy-safe payloads that make each event type complete.
_COMPLETE_FIELDS: dict[TraceEventType, dict[str, object]] = {
    TraceEventType.agent_started: {"input_summary": {"channel": "telegram"}},
    TraceEventType.context_selected: {"context_refs": ["agent_contract"]},
    TraceEventType.llm_called: {"model": "eval-model"},
    TraceEventType.tool_called: {"tool_call": {"name": "calendar.create_event"}},
    TraceEventType.guardrail_checked: {"validation": {"status": "passed"}},
    TraceEventType.approval_requested: {
        "tool_call": {"name": "calendar.create_event", "tier": "P3"}
    },
    TraceEventType.agent_completed: {"output_summary": {"status": "completed"}},
    TraceEventType.agent_failed: {"error": {"type": "EvalError"}},
}

#: Empty value that makes each required field missing again.
_EMPTY_VALUES: dict[str, object] = {
    "input_summary": {},
    "context_refs": [],
    "model": None,
    "tool_call": {},
    "validation": {},
    "output_summary": {},
    "error": {},
}


class InputModel(StrictModel):
    scenario: Literal["reminder-workflow", "incomplete-event"]
    eventType: str | None = None
    omittedField: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> InputModel:
        if self.scenario == "reminder-workflow":
            if self.eventType is not None or self.omittedField is not None:
                raise ValueError("reminder-workflow forbids eventType and omittedField")
            return self
        if self.eventType is None or self.omittedField is None:
            raise ValueError("incomplete-event requires eventType and omittedField")
        try:
            event_type = TraceEventType(self.eventType)
        except ValueError as exc:
            raise ValueError("eventType is not a trace event type") from exc
        if self.omittedField not in REQUIRED_TRACE_FIELDS[event_type]:
            raise ValueError("omittedField is not required for eventType")
        return self


class ExpectedModel(StrictModel):
    requiredTraceEvents: list[str]
    requiredTraceFields: dict[str, list[str]] = Field(default_factory=dict)
    incompleteEventRejected: bool | None
    persistedEvents: int = Field(ge=0)


def _event(event_type: TraceEventType, **overrides: object) -> TraceEvent:
    kwargs: dict[str, Any] = {
        "agent_id": "personal_assistant",
        "event_type": event_type,
        "tenant_id": TENANT,
        **_COMPLETE_FIELDS[event_type],
        **overrides,
    }
    return TraceEvent(**kwargs)


def _workflow_result() -> dict[str, object]:
    principal = Principal.for_test(
        principal_id="eval-user",
        tenant_id=TENANT,
        permission_tier=PermissionTier.P5,
    )
    calendar = LocalCalendarTool()
    scheduler = ReminderScheduler()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    states = InMemoryWorkflowStateStore()
    traces = TraceRecorder()
    workflow = ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
        unit_of_work=InMemoryReminderUnitOfWork(
            calendar=calendar,
            scheduler=scheduler,
            event_store=event_store,
            outbox=outbox,
            states=states,
        ),
    )
    key = reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel="telegram",
        principal_id=principal.principal_id,
        conversation_id="eval-chat",
        source_event_id="eval-42",
    )
    approval = ApprovalGrant.issue(
        principal=principal,
        action="calendar.create_event",
        resource=f"{key}:calendar",
        tier=PermissionTier.P3,
    )
    request = ReminderWorkflowInput(
        message_id="eval-42",
        source_event_id="eval-42",
        conversation_id="eval-chat",
        text="recuérdame clase el martes a las 17",
        recipient="eval-chat",
        now=NOW,
        idempotency_key=key,
        approval=approval,
    )
    workflow.run(principal, request)
    events = traces.list_for_tenant(principal)
    emitted: list[str] = []
    for event in events:
        if event.event_type.value not in emitted:
            emitted.append(event.event_type.value)
    completeness: dict[str, list[str]] = {}
    for event_type_raw in emitted:
        event_type = TraceEventType(event_type_raw)
        of_type = [event for event in events if event.event_type == event_type]
        completeness[event_type_raw] = [
            field_name
            for field_name in REQUIRED_TRACE_FIELDS[event_type]
            if all(getattr(event, field_name) for event in of_type)
        ]
    return {
        "requiredTraceEvents": emitted,
        "requiredTraceFields": completeness,
        "incompleteEventRejected": None,
        "persistedEvents": len(events),
    }


def _incomplete_result(event_type_raw: str, omitted_field: str) -> dict[str, object]:
    event_type = TraceEventType(event_type_raw)
    recorder = TraceRecorder()
    control = _event(event_type)
    require_trace_completeness(control)
    recorder.write(control)
    incomplete = _event(event_type)
    setattr(incomplete, omitted_field, _EMPTY_VALUES[omitted_field])
    rejected = False
    try:
        recorder.write(incomplete)
    except IncompleteTraceEventError:
        rejected = True
    return {
        "requiredTraceEvents": [],
        "requiredTraceFields": {},
        "incompleteEventRejected": rejected,
        "persistedEvents": len(recorder.list_for_tenant(TENANT)),
    }


def execute(value: InputModel) -> dict[str, object]:
    if value.scenario == "reminder-workflow":
        return _workflow_result()
    assert value.eventType is not None and value.omittedField is not None
    return _incomplete_result(value.eventType, value.omittedField)
