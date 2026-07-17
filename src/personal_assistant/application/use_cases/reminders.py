"""Deterministic L2 workflow for reminder creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import (
    ReminderWorkflowInput,
    ReminderWorkflowResult,
)
from personal_assistant.application.dto.runtime import (
    AgentStatus,
    LLMRequest,
    LLMResult,
)
from personal_assistant.application.ports.calendar import (
    CalendarEventRequest,
    CalendarPort,
)
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.observability import TraceRecorderPort
from personal_assistant.application.ports.prompts import (
    PromptCatalogPort,
    RenderedPrompt,
)
from personal_assistant.application.ports.scheduler import ReminderSchedulerPort
from personal_assistant.application.ports.services import LLMProvider
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.application.services.prompts import (
    REMINDER_EXTRACTION_PROMPT_ID,
    DefaultPromptCatalog,
)
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.domain.common.guardrails import assert_prompt_safe
from personal_assistant.domain.common.permissions import (
    PermissionTier,
    require_approval,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.domain.reminders.models import (
    ParsedReminder,
    ReminderClarificationReason,
    ReminderExtraction,
    ReminderIntent,
    ReminderNeedsClarification,
    ReminderUnsupportedReason,
    UnsupportedReminder,
)
from personal_assistant.domain.reminders.idempotency import (
    ReminderIdempotency,
    ReminderIdempotencyConflict,
    ReminderIdempotencyIdentity,
    ReminderPayload,
    reminder_idempotency_key,
)
from personal_assistant.domain.reminders.parser import extract_reminder
from personal_assistant.domain.reminders.workflow_state import (
    ReminderDraft,
    ReminderWorkflowStep,
)


__all__ = ["ReminderWorkflow", "reminder_idempotency", "reminder_idempotency_key"]


def reminder_idempotency(
    principal: Principal,
    request: ReminderWorkflowInput,
) -> ReminderIdempotency:
    """Build the claim from trusted identity and normalized application input."""

    return ReminderIdempotency(
        identity=ReminderIdempotencyIdentity(
            tenant_id=principal.tenant_id,
            channel=request.channel,
            principal_id=principal.principal_id,
            conversation_id=request.conversation_id,
            source_event_id=request.source_event_id,
        ),
        payload=ReminderPayload(
            text=request.text,
            recipient=request.recipient,
            timezone=request.timezone,
        ),
    )


@dataclass(slots=True)
class ReminderWorkflow:
    calendar: CalendarPort
    scheduler: ReminderSchedulerPort
    event_store: EventStorePort
    outbox: OutboxPort
    states: WorkflowStateStorePort
    traces: TraceRecorderPort
    llm: LLMProvider | None = None
    reminder_minutes_before: int = 30
    prompt_catalog: PromptCatalogPort = field(default_factory=DefaultPromptCatalog)
    replies: AssistantReplies = field(default_factory=AssistantReplies)

    def __post_init__(self) -> None:
        if self.reminder_minutes_before < 1:
            raise ValueError("reminder_minutes_before must be greater than zero")

    def run(
        self, principal: Principal, request: ReminderWorkflowInput
    ) -> ReminderWorkflowResult:
        assert_prompt_safe(request.text)
        idempotency = reminder_idempotency(principal, request)
        effective_key = idempotency.key
        effect_ids = idempotency.effect_ids
        source_event_id = idempotency.identity.source_event_id
        payload_fingerprint = idempotency.payload_fingerprint
        timezone = idempotency.payload.timezone
        if (
            request.idempotency_key is not None
            and request.idempotency_key != effective_key
        ):
            raise ReminderIdempotencyConflict(
                tenant_id=principal.tenant_id,
                idempotency_key=effective_key,
            )
        resume_from_step: str | None = None
        if request.approval is not None:
            require_approval(
                principal=principal,
                tier=PermissionTier.P3,
                approval=request.approval,
                action="calendar.create_event",
                resource=f"{effective_key}:calendar",
            )
            resume_from_step = ReminderWorkflowStep.approval_required.value
        run_id = effective_key
        started = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_started,
            tenant_id=principal.tenant_id,
            input_summary={
                "message_id": request.message_id,
                "source_event_id": source_event_id,
                "channel": request.channel,
            },
        )
        self.traces.write(started)
        guardrail_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.guardrail_checked,
            tenant_id=principal.tenant_id,
            validation={"status": "passed"},
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

        registration = self.states.register_or_replay(
            principal,
            WorkflowState(
                tenant_id=principal.tenant_id,
                workflow_type="reminder.create",
                status=WorkflowStatus.running,
                step=ReminderWorkflowStep.classify.value,
                idempotency_key=effective_key,
                payload_fingerprint=payload_fingerprint,
                data={
                    "message_id": request.message_id,
                    "source_event_id": source_event_id,
                    "channel": request.channel,
                    "conversation_id": request.conversation_id,
                    "timezone": timezone,
                },
            ),
            resume_from_step=resume_from_step,
        )
        existing = (
            registration.state
            if registration.replayed or registration.resumed
            else None
        )
        if existing is not None and existing.status == WorkflowStatus.completed:
            return ReminderWorkflowResult(
                status=AgentStatus.completed,
                intent=ReminderIntent.create,
                reply=self.replies.reminder_duplicate(),
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                calendar_event_id=existing.data.get("calendar_event_id"),
                reminder_id=existing.data.get("reminder_id"),
                reused=True,
                trace_ids=[
                    started.trace_id,
                    guardrail_trace.trace_id,
                    context_trace.trace_id,
                ],
            )

        if (
            existing is not None
            and existing.status == WorkflowStatus.running
            and registration.replayed
        ):
            # A matching concurrent delivery may observe the elected executor,
            # but must never become a second side-effecting executor.
            return ReminderWorkflowResult(
                status=AgentStatus.escalated,
                intent=ReminderIntent.create,
                reply=self.replies.reminder_duplicate(),
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                reused=True,
                trace_ids=[
                    started.trace_id,
                    guardrail_trace.trace_id,
                    context_trace.trace_id,
                ],
            )
        if existing is not None and existing.status == WorkflowStatus.failed:
            return ReminderWorkflowResult(
                status=AgentStatus.failed,
                intent=ReminderIntent.create,
                reply=self.replies.reminder_duplicate(),
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                reused=True,
                trace_ids=[
                    started.trace_id,
                    guardrail_trace.trace_id,
                    context_trace.trace_id,
                ],
            )
        if (
            existing is not None
            and existing.status == WorkflowStatus.waiting_approval
            and request.approval is None
        ):
            if existing.step == ReminderWorkflowStep.approval_required.value:
                draft = ReminderDraft.from_mapping(existing.data)
                return ReminderWorkflowResult(
                    status=AgentStatus.escalated,
                    intent=ReminderIntent.create,
                    reply=self.replies.reminder_needs_approval(draft.title),
                    idempotency_key=effective_key,
                    source_event_id=source_event_id,
                    payload_fingerprint=payload_fingerprint,
                    timezone=timezone,
                    approval_required=True,
                    reused=True,
                    trace_ids=[
                        started.trace_id,
                        guardrail_trace.trace_id,
                        context_trace.trace_id,
                    ],
                )
            try:
                clarification_reason = ReminderClarificationReason(
                    str(existing.data.get("clarification_reason"))
                )
            except ValueError:
                clarification_reason = ReminderClarificationReason.missing_datetime
            reply_id, reply_version, reply = self.replies.reminder_clarification(
                clarification_reason
            )
            return ReminderWorkflowResult(
                status=AgentStatus.needs_clarification,
                intent=ReminderIntent.unsupported,
                reply=reply,
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                clarification_reason=clarification_reason,
                clarification_reply_id=reply_id,
                clarification_reply_version=reply_version,
                reused=True,
                trace_ids=[
                    started.trace_id,
                    guardrail_trace.trace_id,
                    context_trace.trace_id,
                ],
            )

        # A validated grant may atomically resume waiting -> running. Concurrent
        # deliveries observe running and take the non-executing branch above.
        state = registration.state

        llm_trace_id: str | None = None
        extraction: ReminderExtraction | None
        clarification: ReminderNeedsClarification | None = None
        if (
            existing
            and existing.step == ReminderWorkflowStep.approval_required.value
            and request.approval is not None
        ):
            extraction = ReminderDraft.from_mapping(existing.data).to_extraction()
        else:
            parse_result = extract_reminder(
                request.text,
                request.now,
                timezone=request.timezone,
            )
            extraction = (
                parse_result.extraction
                if isinstance(parse_result, ParsedReminder)
                else None
            )
            if isinstance(parse_result, ReminderNeedsClarification):
                clarification = parse_result
            if (
                isinstance(parse_result, UnsupportedReminder)
                and parse_result.reason == ReminderUnsupportedReason.not_a_reminder
                and self.llm is not None
            ):
                extraction, llm_trace_id = self._extract_with_llm(
                    principal,
                    request,
                    run_id=run_id,
                    parent_event_id=context_trace.trace_id,
                )
        if extraction is None:
            clarification_reason = (
                clarification.reason
                if clarification is not None
                else ReminderClarificationReason.missing_datetime
            )
            reply_id, reply_version, reply = self.replies.reminder_clarification(
                clarification_reason
            )
            waiting = state.transition(
                status=WorkflowStatus.waiting_approval,
                step=ReminderWorkflowStep.needs_clarification.value,
                data={
                    "clarification_reason": clarification_reason.value,
                    "clarification_reply_id": reply_id,
                    "clarification_reply_version": reply_version,
                },
            )
            self.states.upsert(principal, waiting)
            return ReminderWorkflowResult(
                status=AgentStatus.needs_clarification,
                intent=ReminderIntent.unsupported,
                reply=reply,
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                clarification_reason=clarification_reason,
                clarification_reply_id=reply_id,
                clarification_reply_version=reply_version,
                trace_ids=[
                    trace_id
                    for trace_id in [
                        started.trace_id,
                        guardrail_trace.trace_id,
                        context_trace.trace_id,
                        llm_trace_id,
                    ]
                    if trace_id is not None
                ],
            )

        if request.approval is None:
            approval = TraceEvent(
                run_id=run_id,
                agent_id="personal_assistant",
                event_type=TraceEventType.approval_requested,
                tenant_id=principal.tenant_id,
                tool_call={
                    "name": "calendar.create_event",
                    "tier": PermissionTier.P3.value,
                },
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
                reply=self.replies.reminder_needs_approval(extraction.title),
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                approval_required=True,
                trace_ids=[
                    trace_id
                    for trace_id in [
                        started.trace_id,
                        guardrail_trace.trace_id,
                        context_trace.trace_id,
                        llm_trace_id,
                        approval.trace_id,
                    ]
                    if trace_id is not None
                ],
            )

        calendar_result = self.calendar.create_event(
            principal,
            CalendarEventRequest(
                event_id=effect_ids.calendar_event_id,
                title=extraction.title,
                starts_at=extraction.starts_at,
                timezone=extraction.timezone,
                idempotency_key=f"{effective_key}:calendar",
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
            ),
            approval=request.approval,
        )
        notice_minutes_before = _notice_minutes_before(
            extraction, default_minutes=self.reminder_minutes_before
        )
        reminder = self.scheduler.schedule_before_event(
            principal,
            calendar_event_id=calendar_result.event_id,
            starts_at=extraction.starts_at,
            channel=request.channel,
            recipient=request.recipient,
            body=self.replies.reminder_notification_body(extraction.title),
            timezone=extraction.timezone,
            source_event_id=source_event_id,
            payload_fingerprint=payload_fingerprint,
            minutes_before=notice_minutes_before,
            idempotency_key=f"{effective_key}:notify",
            reminder_id=effect_ids.reminder_id,
        )
        event = CloudEvent(
            id=effect_ids.reminder_created_event_id,
            type="reminder.created",
            source="personal_assistant.application.reminders",
            subject=reminder.reminder_id,
            tenant_id=principal.tenant_id,
            correlation_id=effective_key,
            causation_id=source_event_id,
            source_event_id=source_event_id,
            payload_fingerprint=payload_fingerprint,
            timezone=extraction.timezone,
            data={
                "calendar_event_id": calendar_result.event_id,
                "reminder_id": reminder.reminder_id,
                "starts_at": extraction.starts_at.isoformat(),
                "timezone": extraction.timezone,
                "notify_at": reminder.notify_at.isoformat(),
                "source_event_id": source_event_id,
                "payload_fingerprint": payload_fingerprint,
            },
        )
        self.event_store.append(principal, event)
        self.outbox.add(
            principal,
            event,
            idempotency_key=f"{effective_key}:outbox",
            message_id=effect_ids.outbox_message_id,
        )
        tool_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.tool_called,
            tenant_id=principal.tenant_id,
            tool_call={
                "name": "calendar.create_event",
                "event_id": calendar_result.event_id,
            },
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
            data={
                "calendar_event_id": calendar_result.event_id,
                "reminder_id": reminder.reminder_id,
                "notify_at": reminder.notify_at.isoformat(),
                "timezone": extraction.timezone,
                "source_event_id": source_event_id,
            },
        )
        self.states.upsert(principal, completed)
        return ReminderWorkflowResult(
            status=AgentStatus.completed,
            intent=ReminderIntent.create,
            reply=self.replies.reminder_created(
                title=extraction.title,
                minutes_before=notice_minutes_before,
                direct_notice=extraction.notify_at is not None,
            ),
            idempotency_key=effective_key,
            source_event_id=source_event_id,
            payload_fingerprint=payload_fingerprint,
            timezone=timezone,
            calendar_event_id=calendar_result.event_id,
            reminder_id=reminder.reminder_id,
            reused=calendar_result.reused,
            trace_ids=[
                trace_id
                for trace_id in [
                    started.trace_id,
                    guardrail_trace.trace_id,
                    context_trace.trace_id,
                    llm_trace_id,
                    tool_trace.trace_id,
                    completed_trace.trace_id,
                ]
                if trace_id is not None
            ],
        )

    def _extract_with_llm(
        self,
        principal: Principal,
        request: ReminderWorkflowInput,
        *,
        run_id: str,
        parent_event_id: str,
    ) -> tuple[ReminderExtraction | None, str]:
        rendered_prompt: RenderedPrompt | None = None
        try:
            rendered_prompt = _render_reminder_extraction_prompt(
                request, prompt_catalog=self.prompt_catalog
            )
            llm_result = self.llm.complete(  # type: ignore[union-attr]
                LLMRequest(
                    schema_name="reminder_extraction",
                    max_tokens=384,
                    temperature=0.0,
                    prompt=rendered_prompt.text,
                ),
                budget=TokenBudget(limit=1_500),
            )
            extraction = _reminder_extraction_from_llm(
                llm_result.data, timezone=request.timezone
            )
            trace = _llm_trace(
                principal=principal,
                run_id=run_id,
                parent_event_id=parent_event_id,
                llm_result=llm_result,
                extraction=extraction,
                prompt=rendered_prompt,
            )
        except Exception as exc:
            trace = TraceEvent(
                run_id=run_id,
                agent_id="personal_assistant",
                event_type=TraceEventType.llm_called,
                tenant_id=principal.tenant_id,
                model="configured",
                input_summary={
                    "schema": "reminder_extraction",
                    **_prompt_trace_summary(rendered_prompt),
                },
                error={"type": exc.__class__.__name__, "message": str(exc)[:240]},
                parent_event_id=parent_event_id,
            )
            extraction = None
        self.traces.write(trace)
        return extraction, trace.trace_id


def _render_reminder_extraction_prompt(
    request: ReminderWorkflowInput,
    *,
    prompt_catalog: PromptCatalogPort,
) -> RenderedPrompt:
    return prompt_catalog.render(
        REMINDER_EXTRACTION_PROMPT_ID,
        {
            "now": request.now.isoformat(),
            "timezone": request.timezone,
            "text": repr(request.text),
        },
    )


def _reminder_extraction_prompt(request: ReminderWorkflowInput) -> str:
    return _render_reminder_extraction_prompt(
        request, prompt_catalog=DefaultPromptCatalog()
    ).text


def _reminder_extraction_from_llm(
    data: dict[str, Any], *, timezone: str
) -> ReminderExtraction | None:
    if not bool(data.get("is_reminder")):
        return None
    title = str(data.get("title") or "").strip()
    starts_at_raw = str(data.get("starts_at") or "").strip()
    confidence = float(data.get("confidence") or 0.0)
    if not title or not starts_at_raw or confidence < 0.65:
        return None
    starts_at = datetime.fromisoformat(starts_at_raw.replace("Z", "+00:00"))
    if starts_at.tzinfo is None or starts_at.utcoffset() is None:
        return None
    notify_at_raw = str(data.get("notify_at") or "").strip()
    notify_at = None
    if notify_at_raw:
        notify_at = datetime.fromisoformat(notify_at_raw.replace("Z", "+00:00"))
        if notify_at.tzinfo is None or notify_at.utcoffset() is None:
            return None
    return ReminderExtraction(
        title=title,
        timezone=timezone,
        starts_at=starts_at,
        notify_at=notify_at,
        confidence=confidence,
    )


def _notice_minutes_before(
    extraction: ReminderExtraction, *, default_minutes: int
) -> int:
    if extraction.notify_at is None:
        return default_minutes
    seconds_before = (extraction.starts_at - extraction.notify_at).total_seconds()
    if seconds_before <= 0:
        return 0
    return max(int(seconds_before // 60), 0)


def _llm_trace(
    *,
    principal: Principal,
    run_id: str,
    parent_event_id: str,
    llm_result: LLMResult,
    extraction: ReminderExtraction | None,
    prompt: RenderedPrompt,
) -> TraceEvent:
    return TraceEvent(
        run_id=run_id,
        agent_id="personal_assistant",
        event_type=TraceEventType.llm_called,
        tenant_id=principal.tenant_id,
        model=llm_result.model,
        input_summary={
            "schema": "reminder_extraction",
            "provider": llm_result.provider,
            **_prompt_trace_summary(prompt),
        },
        output_summary={
            "matched": extraction is not None,
            "input_tokens": llm_result.input_tokens,
            "output_tokens": llm_result.output_tokens,
        },
        parent_event_id=parent_event_id,
    )


def _prompt_trace_summary(prompt: RenderedPrompt | None) -> dict[str, str]:
    if prompt is None:
        return {}
    return {"prompt_id": prompt.prompt_id, "prompt_version": prompt.version}
