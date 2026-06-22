"""Deterministic L2 workflow for reminder creation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from personal_assistant.application.dto.reminders import ReminderWorkflowInput, ReminderWorkflowResult
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.ports.calendar import CalendarEventRequest, CalendarPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.observability import TraceRecorderPort
from personal_assistant.application.ports.scheduler import ReminderSchedulerPort
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.domain.common.guardrails import assert_prompt_safe
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.domain.reminders.models import ReminderIntent
from personal_assistant.domain.reminders.parser import extract_reminder
from personal_assistant.domain.reminders.workflow_state import ReminderDraft, ReminderWorkflowStep


def reminder_idempotency_key(tenant_id: str, message_id: str, text: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{message_id}:{text}".encode("utf-8")).hexdigest()[:24]
    return f"reminder:{digest}"


@dataclass(slots=True)
class ReminderWorkflow:
    calendar: CalendarPort
    scheduler: ReminderSchedulerPort
    event_store: EventStorePort
    outbox: OutboxPort
    states: WorkflowStateStorePort
    traces: TraceRecorderPort

    def run(self, principal: Principal, request: ReminderWorkflowInput) -> ReminderWorkflowResult:
        assert_prompt_safe(request.text)
        effective_key = request.idempotency_key or reminder_idempotency_key(
            principal.tenant_id,
            request.message_id,
            request.text,
        )
        run_id = effective_key
        started = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_started,
            tenant_id=principal.tenant_id,
            input_summary={"message_id": request.message_id, "channel": request.channel},
        )
        self.traces.write(started)
        guardrail_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.guardrail_checked,
            tenant_id=principal.tenant_id,
            validation={"input": "passed"},
            parent_event_id=started.trace_id,
        )
        context_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.context_selected,
            tenant_id=principal.tenant_id,
            context_refs=["agent_contract", "current_message", "principal"],
            parent_event_id=guardrail_trace.trace_id,
        )
        self.traces.write(guardrail_trace)
        self.traces.write(context_trace)

        existing = self.states.get_by_idempotency_key(principal, effective_key)
        if existing and existing.status == WorkflowStatus.completed:
            return ReminderWorkflowResult(
                status=AgentStatus.completed,
                intent=ReminderIntent.create,
                reply="Ya tenía ese recordatorio registrado.",
                calendar_event_id=existing.data.get("calendar_event_id"),
                reminder_id=existing.data.get("reminder_id"),
                reused=True,
                trace_ids=[started.trace_id, guardrail_trace.trace_id, context_trace.trace_id],
            )

        state = existing or WorkflowState(
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.running,
            step=ReminderWorkflowStep.classify.value,
            idempotency_key=effective_key,
        )
        self.states.upsert(principal, state)

        if existing and existing.step == ReminderWorkflowStep.approval_required.value and request.approval is not None:
            extraction = ReminderDraft.from_mapping(existing.data).to_extraction()
        else:
            extraction = extract_reminder(request.text, request.now)
        if extraction is None:
            waiting = state.transition(
                status=WorkflowStatus.waiting_approval,
                step=ReminderWorkflowStep.needs_clarification.value,
            )
            self.states.upsert(principal, waiting)
            return ReminderWorkflowResult(
                status=AgentStatus.needs_clarification,
                intent=ReminderIntent.unsupported,
                reply="Necesito una fecha y hora claras para crear el recordatorio.",
                trace_ids=[started.trace_id],
            )

        if request.approval is None:
            approval = TraceEvent(
                run_id=run_id,
                agent_id="personal_assistant",
                event_type=TraceEventType.approval_requested,
                tenant_id=principal.tenant_id,
                tool_call={"name": "calendar.create_event", "tier": PermissionTier.P3.value},
                parent_event_id=context_trace.trace_id,
            )
            self.traces.write(approval)
            waiting = state.transition(
                status=WorkflowStatus.waiting_approval,
                step=ReminderWorkflowStep.approval_required.value,
                data=ReminderDraft.from_extraction(extraction).to_workflow_data(),
            )
            self.states.upsert(principal, waiting)
            return ReminderWorkflowResult(
                status=AgentStatus.escalated,
                intent=ReminderIntent.create,
                reply=f"Puedo crear '{extraction.title}', pero necesito aprobación para escribir en calendario.",
                approval_required=True,
                trace_ids=[started.trace_id, guardrail_trace.trace_id, context_trace.trace_id, approval.trace_id],
            )

        calendar_result = self.calendar.create_event(
            principal,
            CalendarEventRequest(
                title=extraction.title,
                starts_at=extraction.starts_at,
                timezone=request.timezone,
                idempotency_key=f"{effective_key}:calendar",
            ),
            approval=request.approval,
        )
        reminder = self.scheduler.schedule_before_event(
            principal,
            calendar_event_id=calendar_result.event_id,
            starts_at=extraction.starts_at,
            channel=request.channel,
            recipient=request.recipient,
            body=f"Recordatorio: {extraction.title}",
            idempotency_key=f"{effective_key}:notify",
        )
        event = CloudEvent(
            type="reminder.created",
            source="personal_assistant.application.reminders",
            subject=reminder.reminder_id,
            tenant_id=principal.tenant_id,
            data={
                "calendar_event_id": calendar_result.event_id,
                "reminder_id": reminder.reminder_id,
                "starts_at": extraction.starts_at.isoformat(),
            },
        )
        self.event_store.append(principal, event)
        self.outbox.add(principal, event, idempotency_key=f"{effective_key}:outbox")
        tool_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.tool_called,
            tenant_id=principal.tenant_id,
            tool_call={"name": "calendar.create_event", "event_id": calendar_result.event_id},
            parent_event_id=context_trace.trace_id,
        )
        completed_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_completed,
            tenant_id=principal.tenant_id,
            output_summary={"reminder_id": reminder.reminder_id},
            parent_event_id=tool_trace.trace_id,
        )
        self.traces.write(tool_trace)
        self.traces.write(completed_trace)
        completed = state.transition(
            status=WorkflowStatus.completed,
            step=ReminderWorkflowStep.completed.value,
            data={"calendar_event_id": calendar_result.event_id, "reminder_id": reminder.reminder_id},
        )
        self.states.upsert(principal, completed)
        return ReminderWorkflowResult(
            status=AgentStatus.completed,
            intent=ReminderIntent.create,
            reply=f"Listo. Te recordaré {extraction.title} 30 minutos antes.",
            calendar_event_id=calendar_result.event_id,
            reminder_id=reminder.reminder_id,
            reused=calendar_result.reused,
            trace_ids=[
                started.trace_id,
                guardrail_trace.trace_id,
                context_trace.trace_id,
                tool_trace.trace_id,
                completed_trace.trace_id,
            ],
        )
