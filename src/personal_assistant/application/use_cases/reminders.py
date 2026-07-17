"""Deterministic L2 workflow for reminder creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any, Literal

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
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderTransaction,
    ReminderUnitOfWork,
)
from personal_assistant.application.ports.scheduler import ReminderSchedulerPort
from personal_assistant.application.ports.services import LLMProvider
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.application.services.prompts import (
    REMINDER_EXTRACTION_PROMPT_ID,
    DefaultPromptCatalog,
)
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.dto.workflows import (
    WorkflowState,
    WorkflowStateRegistration,
    WorkflowStatus,
)
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
class _DirectReminderTransaction:
    """Compatibility transaction for custom v1 compositions without a UoW.

    Runtime composition always supplies a real unit of work. This adapter keeps
    direct construction source-compatible while third-party persistence
    adapters migrate to the new contract; it cannot compensate partial writes.
    """

    calendar: CalendarPort
    scheduler: ReminderSchedulerPort
    event_store: EventStorePort
    outbox: OutboxPort
    states: WorkflowStateStorePort

    def __enter__(self) -> _DirectReminderTransaction:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        return False

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


@dataclass(slots=True)
class ReminderWorkflow:
    calendar: CalendarPort
    scheduler: ReminderSchedulerPort
    event_store: EventStorePort
    outbox: OutboxPort
    states: WorkflowStateStorePort
    traces: TraceRecorderPort
    unit_of_work: ReminderUnitOfWork | None = None
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

        initial_state = WorkflowState(
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
        )
        base_trace_ids = [
            started.trace_id,
            guardrail_trace.trace_id,
            context_trace.trace_id,
        ]

        if request.approval is None:
            return self._run_before_approval(
                principal,
                request,
                initial_state=initial_state,
                run_id=run_id,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                context_trace_id=context_trace.trace_id,
                base_trace_ids=base_trace_ids,
            )

        existing_before_transaction = self.states.get_by_idempotency_key(
            principal, effective_key
        )
        if (
            existing_before_transaction is not None
            and existing_before_transaction.payload_fingerprint != payload_fingerprint
        ):
            raise ReminderIdempotencyConflict(
                tenant_id=principal.tenant_id,
                idempotency_key=effective_key,
            )

        llm_trace_id: str | None = None
        extraction: ReminderExtraction | None = None
        clarification: ReminderNeedsClarification | None = None
        if (
            existing_before_transaction is not None
            and existing_before_transaction.status == WorkflowStatus.waiting_approval
            and existing_before_transaction.step
            == ReminderWorkflowStep.approval_required.value
        ):
            extraction = ReminderDraft.from_mapping(
                existing_before_transaction.data
            ).to_extraction()
        elif existing_before_transaction is None:
            extraction, clarification, llm_trace_id = self._resolve_extraction(
                principal,
                request,
                run_id=run_id,
                parent_event_id=context_trace.trace_id,
            )

        calendar_result = None
        reminder = None
        notice_minutes_before = self.reminder_minutes_before
        with self._begin_transaction(principal) as transaction:
            registration = transaction.states.register_or_replay(
                principal,
                initial_state,
                resume_from_step=resume_from_step,
            )
            replay = self._registration_result(
                registration,
                effective_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                trace_ids=base_trace_ids,
            )
            if replay is not None:
                transaction.commit()
                return replay

            state = registration.state
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
                transaction.states.upsert(principal, waiting)
                transaction.commit()
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
                        for trace_id in [*base_trace_ids, llm_trace_id]
                        if trace_id is not None
                    ],
                )

            calendar_result = transaction.calendar.create_event(
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
            reminder = transaction.scheduler.schedule_before_event(
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
            reminder_created = CloudEvent(
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
            transaction.event_store.append(principal, reminder_created)
            notification_requested = CloudEvent(
                id=effect_ids.notification_requested_event_id,
                type="notification.requested",
                source="personal_assistant.application.reminders",
                subject=reminder.reminder_id,
                tenant_id=principal.tenant_id,
                correlation_id=effective_key,
                causation_id=reminder_created.id,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=extraction.timezone,
                data={
                    "calendar_event_id": calendar_result.event_id,
                    "reminder_id": reminder.reminder_id,
                    "channel": request.channel,
                    "recipient": request.recipient,
                    "body": reminder.body,
                    "notify_at": reminder.notify_at.isoformat(),
                    "timezone": extraction.timezone,
                    "source_event_id": source_event_id,
                    "payload_fingerprint": payload_fingerprint,
                },
            )
            transaction.outbox.add(
                principal,
                notification_requested,
                idempotency_key=f"{effective_key}:outbox",
                next_attempt_at=reminder.notify_at,
                message_id=effect_ids.outbox_message_id,
            )
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
            transaction.states.upsert(principal, completed)
            transaction.commit()

        if calendar_result is None or reminder is None or extraction is None:
            raise RuntimeError("committed reminder transaction produced no result")
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
        self.traces.write(tool_trace)
        completed_trace = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_completed,
            tenant_id=principal.tenant_id,
            output_summary={"reminder_id": reminder.reminder_id},
            parent_event_id=tool_trace.trace_id,
        )
        self.traces.write(completed_trace)
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

    def _run_before_approval(
        self,
        principal: Principal,
        request: ReminderWorkflowInput,
        *,
        initial_state: WorkflowState,
        run_id: str,
        source_event_id: str,
        payload_fingerprint: str,
        timezone: str,
        context_trace_id: str,
        base_trace_ids: list[str],
    ) -> ReminderWorkflowResult:
        registration = self.states.register_or_replay(principal, initial_state)
        replay = self._registration_result(
            registration,
            effective_key=initial_state.idempotency_key,
            source_event_id=source_event_id,
            payload_fingerprint=payload_fingerprint,
            timezone=timezone,
            trace_ids=base_trace_ids,
        )
        if replay is not None:
            return replay

        extraction, clarification, llm_trace_id = self._resolve_extraction(
            principal,
            request,
            run_id=run_id,
            parent_event_id=context_trace_id,
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
            waiting = registration.state.transition(
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
                idempotency_key=initial_state.idempotency_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                clarification_reason=clarification_reason,
                clarification_reply_id=reply_id,
                clarification_reply_version=reply_version,
                trace_ids=[
                    trace_id
                    for trace_id in [*base_trace_ids, llm_trace_id]
                    if trace_id is not None
                ],
            )

        approval = TraceEvent(
            run_id=run_id,
            agent_id="personal_assistant",
            event_type=TraceEventType.approval_requested,
            tenant_id=principal.tenant_id,
            tool_call={
                "name": "calendar.create_event",
                "tier": PermissionTier.P3.value,
            },
            parent_event_id=context_trace_id,
        )
        self.traces.write(approval)
        waiting = registration.state.transition(
            status=WorkflowStatus.waiting_approval,
            step=ReminderWorkflowStep.approval_required.value,
            data=ReminderDraft.from_extraction(extraction).to_workflow_data(),
        )
        self.states.upsert(principal, waiting)
        return ReminderWorkflowResult(
            status=AgentStatus.escalated,
            intent=ReminderIntent.create,
            reply=self.replies.reminder_needs_approval(extraction.title),
            idempotency_key=initial_state.idempotency_key,
            source_event_id=source_event_id,
            payload_fingerprint=payload_fingerprint,
            timezone=timezone,
            approval_required=True,
            trace_ids=[
                trace_id
                for trace_id in [*base_trace_ids, llm_trace_id, approval.trace_id]
                if trace_id is not None
            ],
        )

    def _registration_result(
        self,
        registration: WorkflowStateRegistration,
        *,
        effective_key: str,
        source_event_id: str,
        payload_fingerprint: str,
        timezone: str,
        trace_ids: list[str],
    ) -> ReminderWorkflowResult | None:
        if not registration.replayed:
            return None
        existing = registration.state
        if existing.status == WorkflowStatus.completed:
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
                trace_ids=trace_ids,
            )
        if existing.status == WorkflowStatus.running:
            return ReminderWorkflowResult(
                status=AgentStatus.escalated,
                intent=ReminderIntent.create,
                reply=self.replies.reminder_duplicate(),
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                reused=True,
                trace_ids=trace_ids,
            )
        if existing.status == WorkflowStatus.failed:
            return ReminderWorkflowResult(
                status=AgentStatus.failed,
                intent=ReminderIntent.create,
                reply=self.replies.reminder_duplicate(),
                idempotency_key=effective_key,
                source_event_id=source_event_id,
                payload_fingerprint=payload_fingerprint,
                timezone=timezone,
                reused=True,
                trace_ids=trace_ids,
            )
        if existing.status != WorkflowStatus.waiting_approval:
            return None
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
                trace_ids=trace_ids,
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
            trace_ids=trace_ids,
        )

    def _resolve_extraction(
        self,
        principal: Principal,
        request: ReminderWorkflowInput,
        *,
        run_id: str,
        parent_event_id: str,
    ) -> tuple[
        ReminderExtraction | None,
        ReminderNeedsClarification | None,
        str | None,
    ]:
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
        clarification = (
            parse_result
            if isinstance(parse_result, ReminderNeedsClarification)
            else None
        )
        llm_trace_id: str | None = None
        if (
            isinstance(parse_result, UnsupportedReminder)
            and parse_result.reason == ReminderUnsupportedReason.not_a_reminder
            and self.llm is not None
        ):
            extraction, llm_trace_id = self._extract_with_llm(
                principal,
                request,
                run_id=run_id,
                parent_event_id=parent_event_id,
            )
        return extraction, clarification, llm_trace_id

    def _begin_transaction(self, principal: Principal) -> ReminderTransaction:
        if self.unit_of_work is not None:
            return self.unit_of_work.begin(principal)
        return _DirectReminderTransaction(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
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
