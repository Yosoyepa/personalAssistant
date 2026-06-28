"""Deterministic command router for channel messages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from personal_assistant.application.dto.channels import NormalizedMessage
from personal_assistant.application.dto.commands import CommandKind, CommandResult, PendingApproval
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.ports.approvals import ApprovalStorePort
from personal_assistant.application.ports.calendar import CalendarReadPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.application.use_cases.reminders import ReminderWorkflow, reminder_idempotency_key
from personal_assistant.domain.common.exceptions import AssistantError
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier


HELP_TEXT = "\n".join(
    [
        "Comandos disponibles:",
        "/start - inicia la conversación.",
        "/help - muestra esta ayuda.",
        "/recordar <texto> - crea un recordatorio con aprobación.",
        "/agenda - lista eventos locales.",
        "/pendientes - muestra aprobaciones pendientes.",
        "/aprobar <id> - aprueba una acción pendiente.",
        "/cancelar <id> - cancela una aprobación pendiente.",
        "/status - muestra el estado local del asistente.",
    ]
)


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
            "agéndame ",
            "agendame ",
            "agendarme ",
            "agenda ",
            "agendar ",
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


@dataclass(slots=True)
class ConversationCommandService:
    approvals: ApprovalStorePort
    calendar: CalendarReadPort
    reminder_workflow: ReminderWorkflow
    states: WorkflowStateStorePort
    event_store: EventStorePort
    outbox: OutboxPort

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
                reply="Asistente personal activo. Usa /help para ver comandos.",
            )
        if command == "help" or lowered == "/help":
            return CommandResult(status=AgentStatus.completed, kind=CommandKind.help, reply=HELP_TEXT)
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
        if _looks_like_reminder(text):
            return self._create_reminder(principal, message, text=_extract_reminder_text(text), now=now, timezone=timezone)
        return CommandResult(
            status=AgentStatus.declined,
            kind=CommandKind.unsupported,
            reply="No reconocí ese comando. Usa /help para ver opciones.",
            dispatch_required=True,
        )

    def _status(self, principal: Principal) -> CommandResult:
        pending_count = len(self.approvals.list_pending(principal))
        state_count = len(self.states.list_for_tenant(principal))
        event_count = len(self.event_store.list_for_tenant(principal))
        list_outbox = getattr(self.outbox, "list_for_tenant", None)
        outbox_count = len(list_outbox(principal)) if callable(list_outbox) else 0
        reply = (
            "Estado local: activo. "
            f"Pendientes: {pending_count}. Workflows: {state_count}. Eventos: {event_count}. Outbox: {outbox_count}."
        )
        return CommandResult(status=AgentStatus.completed, kind=CommandKind.status, reply=reply)

    def _agenda(self, principal: Principal) -> CommandResult:
        events = sorted(self.calendar.list_events(principal), key=lambda event: event.starts_at)
        if not events:
            return CommandResult(
                status=AgentStatus.completed,
                kind=CommandKind.agenda,
                reply="No hay eventos locales registrados.",
            )
        lines = ["Agenda local:"]
        for event in events[:10]:
            lines.append(f"- {event.starts_at.isoformat()} | {event.title} ({event.event_id})")
        return CommandResult(status=AgentStatus.completed, kind=CommandKind.agenda, reply="\n".join(lines))

    def _pending(self, principal: Principal) -> CommandResult:
        pending = self.approvals.list_pending(principal)
        if not pending:
            return CommandResult(
                status=AgentStatus.completed,
                kind=CommandKind.pending_approvals,
                reply="No tienes aprobaciones pendientes.",
            )
        lines = ["Aprobaciones pendientes:"]
        for approval in pending:
            lines.append(f"- {approval.approval_id}: {approval.action} para '{approval.request_text}'")
        lines.append("Usa /aprobar <id> o /cancelar <id>.")
        return CommandResult(status=AgentStatus.escalated, kind=CommandKind.pending_approvals, reply="\n".join(lines))

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
                reply="Indica qué quieres recordar: /recordar <texto>",
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
                reply=f"{result.reply}\nAprueba con /aprobar {approval.approval_id}",
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
                reply="Indica el id: /aprobar <id>",
            )
        approval = self.approvals.get(principal, parts[1].strip())
        if approval is None:
            return CommandResult(status=AgentStatus.failed, kind=CommandKind.approve, reply="No encontré esa aprobación.")
        if approval.workflow_kind != "reminder.create":
            return CommandResult(
                status=AgentStatus.failed,
                kind=CommandKind.approve,
                reply="Ese tipo de aprobación todavía no está soportado.",
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
                reply="Indica el id: /cancelar <id>",
            )
        try:
            self.approvals.reject(principal, parts[1].strip())
        except AssistantError as exc:
            return CommandResult(status=AgentStatus.failed, kind=CommandKind.cancel, reply=str(exc))
        return CommandResult(status=AgentStatus.completed, kind=CommandKind.cancel, reply="Aprobación cancelada.")
