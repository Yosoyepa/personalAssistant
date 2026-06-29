"""Deterministic command router for channel messages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from personal_assistant.application.dto.channels import NormalizedMessage
from personal_assistant.application.dto.commands import (
    CommandKind,
    CommandResult,
    InferredCommandIntent,
    PendingApproval,
)
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus, LLMRequest
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.ports.observability import TraceRecorderPort
from personal_assistant.application.ports.approvals import ApprovalStorePort
from personal_assistant.application.ports.calendar import CalendarReadPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.prompts import PromptCatalogPort, RenderedPrompt
from personal_assistant.application.ports.services import LLMProvider
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.application.services.prompts import (
    CONVERSATION_INTENT_PROMPT_ID,
    DefaultPromptCatalog,
)
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.use_cases.reminders import ReminderWorkflow, reminder_idempotency_key
from personal_assistant.domain.common.exceptions import AssistantError
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier


LLM_INTENT_CONFIDENCE_THRESHOLD = 0.65


def _approval_id(*, tenant_id: str, principal_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{principal_id}:{idempotency_key}".encode("utf-8")).hexdigest()[:10]
    return f"ap_{digest}"


def _looks_like_reminder(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized.startswith(
        (
            "/recordar ",
            "recuérdame ",
            "recuerdame ",
            "recordarme ",
            "puedes recordarme ",
            "podrías recordarme ",
            "necesito que me recuerdes ",
            "quiero que me recuerdes ",
            "me recuerdas ",
            "me recuerdes ",
            "agéndame ",
            "agendame ",
            "agendarme ",
            "agenda ",
            "agendar ",
            "avísame ",
            "avisame ",
            "me avisas ",
            "me puedes avisar ",
        )
    ) or ("cita" in normalized and "las " in normalized)


def _extract_reminder_text(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("/recordar "):
        return stripped[len("/recordar ") :].strip()
    if lowered.startswith("/"):
        parts = stripped.split(maxsplit=1)
        return parts[1].strip() if len(parts) == 2 else ""
    return stripped


def _workflow_text_from_inferred_reminder(original_text: str, reminder_text: str) -> str:
    normalized = reminder_text.strip()
    if not normalized:
        return original_text
    return f"recordatorio {normalized}"


@dataclass(slots=True)
class ConversationCommandService:
    approvals: ApprovalStorePort
    calendar: CalendarReadPort
    reminder_workflow: ReminderWorkflow
    states: WorkflowStateStorePort
    event_store: EventStorePort
    outbox: OutboxPort
    llm: LLMProvider | None = None
    prompt_catalog: PromptCatalogPort = field(default_factory=DefaultPromptCatalog)
    traces: TraceRecorderPort | None = None
    replies: AssistantReplies = field(default_factory=AssistantReplies)

    def handle(
        self,
        principal: Principal,
        message: NormalizedMessage,
        *,
        now: datetime,
        timezone: str,
    ) -> CommandResult:
        text = message.text.strip()
        lowered = text.lower()
        command = message.command
        if command == "start" or lowered == "/start":
            return CommandResult(
                status=AgentStatus.completed,
                kind=CommandKind.start,
                reply=self.replies.start(),
            )
        if command == "help" or lowered == "/help":
            return CommandResult(status=AgentStatus.completed, kind=CommandKind.help, reply=self.replies.help())
        if command == "status" or lowered == "/status":
            return self._status(principal)
        if command == "agenda" or lowered == "/agenda":
            return self._agenda(principal)
        if command == "pendientes" or lowered == "/pendientes":
            return self._pending(principal)
        if command == "aprobar" or lowered.startswith("/aprobar"):
            return self._approve(principal, text, now=now, timezone=timezone)
        if command == "cancelar" or lowered.startswith("/cancelar"):
            return self._cancel(principal, text)
        if command == "recordar":
            return self._create_reminder(
                principal,
                message,
                text=message.command_args.strip(),
                now=now,
                timezone=timezone,
            )
        if not lowered.startswith("/"):
            inferred = self._infer_intent(principal, message, text, now=now, timezone=timezone)
            if inferred is not None:
                return self._handle_inferred_intent(principal, message, inferred, now=now, timezone=timezone)
        if _looks_like_reminder(text):
            return self._create_reminder(
                principal,
                message,
                text=_extract_reminder_text(text),
                now=now,
                timezone=timezone,
            )
        return CommandResult(
            status=AgentStatus.declined,
            kind=CommandKind.unsupported,
            reply=self.replies.unsupported(),
            dispatch_required=True,
        )

    def _status(self, principal: Principal) -> CommandResult:
        pending_count = len(self.approvals.list_pending(principal))
        state_count = len(self.states.list_for_tenant(principal))
        event_count = len(self.event_store.list_for_tenant(principal))
        outbox_count = len(self.outbox.list_for_tenant(principal))
        reply = self.replies.status(
            pending_count=pending_count,
            state_count=state_count,
            event_count=event_count,
            outbox_count=outbox_count,
        )
        return CommandResult(status=AgentStatus.completed, kind=CommandKind.status, reply=reply)

    def _agenda(self, principal: Principal) -> CommandResult:
        events = sorted(self.calendar.list_events(principal), key=lambda event: event.starts_at)
        if not events:
            return CommandResult(
                status=AgentStatus.completed,
                kind=CommandKind.agenda,
                reply=self.replies.agenda_empty(),
            )
        rows = [(event.starts_at, event.title, event.event_id) for event in events[:10]]
        return CommandResult(status=AgentStatus.completed, kind=CommandKind.agenda, reply=self.replies.agenda(rows))

    def _pending(self, principal: Principal) -> CommandResult:
        pending = self.approvals.list_pending(principal)
        if not pending:
            return CommandResult(
                status=AgentStatus.completed,
                kind=CommandKind.pending_approvals,
                reply=self.replies.pending_empty(),
            )
        rows = [(approval.approval_id, approval.action, approval.request_text) for approval in pending]
        return CommandResult(
            status=AgentStatus.escalated,
            kind=CommandKind.pending_approvals,
            reply=self.replies.pending_approvals(rows),
        )

    def _create_reminder(
        self,
        principal: Principal,
        message: NormalizedMessage,
        *,
        text: str,
        now: datetime,
        timezone: str,
    ) -> CommandResult:
        if not text.strip():
            return CommandResult(
                status=AgentStatus.needs_clarification,
                kind=CommandKind.reminder_create,
                reply=self.replies.reminder_missing_text(),
            )
        idempotency_key = reminder_idempotency_key(principal.tenant_id, message.message_id, text)
        result = self.reminder_workflow.run(
            principal,
            ReminderWorkflowInput(
                message_id=message.message_id,
                conversation_id=message.conversation_id,
                text=text,
                channel=message.channel.value,
                recipient=message.conversation_id,
                now=now,
                timezone=timezone,
                idempotency_key=idempotency_key,
                approval=None,
            ),
        )
        if result.approval_required:
            approval = self.approvals.create(
                principal,
                PendingApproval(
                    approval_id=_approval_id(
                        tenant_id=principal.tenant_id,
                        principal_id=principal.principal_id,
                        idempotency_key=idempotency_key,
                    ),
                    tenant_id=principal.tenant_id,
                    principal_id=principal.principal_id,
                    action="calendar.create_event",
                    resource=f"{idempotency_key}:calendar",
                    tier=PermissionTier.P3.value,
                    workflow_kind="reminder.create",
                    message_id=message.message_id,
                    conversation_id=message.conversation_id,
                    channel=message.channel.value,
                    recipient=message.conversation_id,
                    request_text=text,
                    idempotency_key=idempotency_key,
                ),
            )
            return CommandResult(
                status=AgentStatus.escalated,
                kind=CommandKind.reminder_create,
                reply=f"{result.reply}\n{self.replies.approval_command_hint(approval.approval_id)}",
                approval_id=approval.approval_id,
                metadata={"approval_required": True},
            )
        return CommandResult(status=result.status, kind=CommandKind.reminder_create, reply=result.reply)

    def _approve(self, principal: Principal, text: str, *, now: datetime, timezone: str) -> CommandResult:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return CommandResult(
                status=AgentStatus.needs_clarification,
                kind=CommandKind.approve,
                reply=self.replies.approve_missing_id(),
            )
        approval = self.approvals.get(principal, parts[1].strip())
        if approval is None:
            return CommandResult(
                status=AgentStatus.failed,
                kind=CommandKind.approve,
                reply=self.replies.approval_not_found(),
            )
        if approval.workflow_kind != "reminder.create":
            return CommandResult(
                status=AgentStatus.failed,
                kind=CommandKind.approve,
                reply=self.replies.approval_type_unsupported(),
            )
        try:
            grant = self.approvals.approve(principal, approval.approval_id)
        except AssistantError as exc:
            return CommandResult(status=AgentStatus.failed, kind=CommandKind.approve, reply=str(exc))
        result = self.reminder_workflow.run(
            principal,
            ReminderWorkflowInput(
                message_id=approval.message_id,
                conversation_id=approval.conversation_id,
                text=approval.request_text,
                channel=approval.channel,  # type: ignore[arg-type]
                recipient=approval.recipient,
                now=now,
                timezone=timezone,
                idempotency_key=approval.idempotency_key,
                approval=grant,
            ),
        )
        return CommandResult(status=result.status, kind=CommandKind.approve, reply=result.reply)

    def _cancel(self, principal: Principal, text: str) -> CommandResult:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return CommandResult(
                status=AgentStatus.needs_clarification,
                kind=CommandKind.cancel,
                reply=self.replies.cancel_missing_id(),
            )
        try:
            self.approvals.reject(principal, parts[1].strip())
        except AssistantError as exc:
            return CommandResult(status=AgentStatus.failed, kind=CommandKind.cancel, reply=str(exc))
        return CommandResult(status=AgentStatus.completed, kind=CommandKind.cancel, reply=self.replies.approval_cancelled())

    def _infer_intent(
        self,
        principal: Principal,
        message: NormalizedMessage,
        text: str,
        *,
        now: datetime,
        timezone: str,
    ) -> InferredCommandIntent | None:
        if self.llm is None:
            return None
        run_id = f"command:{message.channel.value}:{message.conversation_id}:{message.message_id}:intent"
        rendered_prompt: RenderedPrompt | None = None
        try:
            rendered_prompt = _render_intent_prompt(
                text=text,
                now=now,
                timezone=timezone,
                prompt_catalog=self.prompt_catalog,
            )
            result = self.llm.complete(
                LLMRequest(
                    schema_name="conversation_intent",
                    max_tokens=256,
                    temperature=0.0,
                    prompt=rendered_prompt.text,
                ),
                budget=TokenBudget(limit=1_000),
            )
            inferred = InferredCommandIntent.model_validate(result.data)
        except Exception as exc:
            self._write_trace(
                TraceEvent(
                    run_id=run_id,
                    agent_id="personal_assistant",
                    event_type=TraceEventType.agent_failed,
                    tenant_id=principal.tenant_id,
                    input_summary={
                        "text": text[:500],
                        "source": message.channel.value,
                        **_prompt_trace_summary(rendered_prompt),
                    },
                    error={"type": exc.__class__.__name__, "message": str(exc)[:500]},
                )
            )
            return None
        accepted = inferred.confidence >= LLM_INTENT_CONFIDENCE_THRESHOLD
        self._write_trace(
            TraceEvent(
                run_id=run_id,
                agent_id="personal_assistant",
                event_type=TraceEventType.llm_called,
                tenant_id=principal.tenant_id,
                input_summary={
                    "text": text[:500],
                    "source": message.channel.value,
                    **_prompt_trace_summary(rendered_prompt),
                },
                model=result.model,
                output_summary={
                    "kind": inferred.kind.value,
                    "confidence": inferred.confidence,
                    "reminder_text": inferred.reminder_text,
                    "accepted": accepted,
                    "threshold": LLM_INTENT_CONFIDENCE_THRESHOLD,
                },
            )
        )
        if not accepted:
            return None
        return inferred

    def _write_trace(self, event: TraceEvent) -> None:
        if self.traces is not None:
            self.traces.write(event)

    def _handle_inferred_intent(
        self,
        principal: Principal,
        message: NormalizedMessage,
        inferred: InferredCommandIntent,
        *,
        now: datetime,
        timezone: str,
    ) -> CommandResult:
        if inferred.kind == CommandKind.reminder_create and inferred.reminder_text:
            return self._create_reminder(
                principal,
                message,
                text=_workflow_text_from_inferred_reminder(message.text, inferred.reminder_text),
                now=now,
                timezone=timezone,
            )
        if inferred.kind == CommandKind.help:
            return CommandResult(status=AgentStatus.completed, kind=CommandKind.help, reply=self.replies.help())
        if inferred.kind == CommandKind.status:
            return self._status(principal)
        if inferred.kind == CommandKind.agenda:
            return self._agenda(principal)
        if inferred.kind == CommandKind.pending_approvals:
            return self._pending(principal)
        return CommandResult(
            status=AgentStatus.declined,
            kind=CommandKind.unsupported,
            reply=self.replies.unsupported(),
            dispatch_required=True,
        )


def _allowed_free_text_intents() -> tuple[CommandKind, ...]:
    return (
        CommandKind.reminder_create,
        CommandKind.agenda,
        CommandKind.pending_approvals,
        CommandKind.status,
        CommandKind.help,
        CommandKind.unsupported,
    )


def _render_intent_prompt(
    *,
    text: str,
    now: datetime,
    timezone: str,
    prompt_catalog: PromptCatalogPort,
) -> RenderedPrompt:
    return prompt_catalog.render(
        CONVERSATION_INTENT_PROMPT_ID,
        {
            "allowed_intents": [kind.value for kind in _allowed_free_text_intents()],
            "now": now.isoformat(),
            "timezone": timezone,
            "text": repr(text),
        },
    )


def _intent_prompt(*, text: str, now: datetime, timezone: str) -> str:
    return _render_intent_prompt(
        text=text,
        now=now,
        timezone=timezone,
        prompt_catalog=DefaultPromptCatalog(),
    ).text


def _prompt_trace_summary(prompt: RenderedPrompt | None) -> dict[str, str]:
    if prompt is None:
        return {}
    return {"prompt_id": prompt.prompt_id, "prompt_version": prompt.version}
