"""Deterministic L2 workflow for reminder creation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.calendar.local import CalendarEventRequest, LocalCalendarTool
from personal_assistant.reminders.models import (
    ReminderExtraction,
    ReminderIntent,
    ReminderWorkflowInput,
    ReminderWorkflowResult,
)
from personal_assistant.scheduler.service import ReminderScheduler
from personal_assistant.domain.common.durable import WorkflowState, WorkflowStatus
from personal_assistant.domain.common.events import CloudEvent
from personal_assistant.domain.common.guardrails import assert_prompt_safe
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.tracing import TraceEvent, TraceEventType, TraceRecorder
from personal_assistant.stores.in_memory import InMemoryEventStore, InMemoryOutbox, InMemoryWorkflowStateStore


SPANISH_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def reminder_idempotency_key(tenant_id: str, message_id: str, text: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{message_id}:{text}".encode("utf-8")).hexdigest()[:24]
    return f"reminder:{digest}"


def _next_weekday(now: datetime, target_weekday: int, *, hour: int, minute: int) -> datetime:
    days_ahead = (target_weekday - now.weekday()) % 7
    candidate = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)).replace(
        hour=hour,
        minute=minute,
    )
    if candidate <= now:
        candidate = candidate + timedelta(days=7)
    return candidate


def extract_reminder(text: str, now: datetime) -> ReminderExtraction | None:
    lowered = _fold_text(text)
    if "recuerd" not in lowered:
        return None

    weekday = next((value for name, value in SPANISH_WEEKDAYS.items() if _fold_text(name) in lowered), None)
    hour_match = re.search(r"\b(?:a las|las|a)\s+(\d{1,2})(?::(\d{2}))?\b", lowered)
    if weekday is None or hour_match is None:
        return None

    hour = int(hour_match.group(1))
    minute = int(hour_match.group(2) or 0)
    if not 0 <= minute <= 59:
        return None
    if "pm" in lowered and hour < 12:
        hour += 12
    if "am" in lowered and hour == 12:
        hour = 0
    if not 0 <= hour <= 23:
        return None

    starts_at = _next_weekday(now, weekday, hour=hour, minute=minute)
    title = re.sub(r"\b(recu[eé]rdame|recordarme|el|la|los|las|a|este|esta)\b", " ", text, flags=re.I)
    for day in SPANISH_WEEKDAYS:
        title = re.sub(day, " ", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" .,:;-") or "Recordatorio"
    return ReminderExtraction(title=title, starts_at=starts_at, confidence=0.86)


@dataclass(slots=True)
class ReminderWorkflow:
    calendar: LocalCalendarTool
    scheduler: ReminderScheduler
    event_store: InMemoryEventStore
    outbox: InMemoryOutbox
    states: InMemoryWorkflowStateStore
    traces: TraceRecorder

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
            step="classify",
            idempotency_key=effective_key,
        )
        self.states.upsert(principal, state)

        if existing and existing.step == "approval_required" and request.approval is not None:
            extraction = ReminderExtraction(
                title=str(existing.data["title"]),
                starts_at=datetime.fromisoformat(str(existing.data["starts_at"])),
                confidence=float(existing.data.get("confidence", 0.86)),
            )
        else:
            extraction = extract_reminder(request.text, request.now)
        if extraction is None:
            waiting = state.transition(status=WorkflowStatus.waiting_approval, step="needs_clarification")
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
                step="approval_required",
                data={
                    "title": extraction.title,
                    "starts_at": extraction.starts_at.isoformat(),
                    "confidence": extraction.confidence,
                },
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
            source="personal_assistant.reminders",
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
            step="completed",
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
