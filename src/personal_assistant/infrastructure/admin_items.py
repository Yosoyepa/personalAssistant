"""Row mappers from runtime DTOs to admin dashboard dictionaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
)
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage
from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.application.dto.workflows import WorkflowState
from personal_assistant.application.ports.calendar import CalendarEventResult
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.memory.models import MemoryRecord
from personal_assistant.infrastructure.admin_redaction import (
    _redacted_admin_payload,
)
from personal_assistant.infrastructure.admin_text import _preview
from personal_assistant.infrastructure.admin_time import (
    _is_on_or_after,
    _iso,
    _reminder_due,
)


def _trace_item(event: TraceEvent) -> dict[str, Any]:
    return {
        "trace_id": event.trace_id,
        "run_id": event.run_id,
        "agent_id": event.agent_id,
        "event_type": event.event_type.value,
        "timestamp": _iso(event.timestamp),
        "input_summary": event.input_summary,
        "context_refs": event.context_refs,
        "tool_call": event.tool_call,
        "model": event.model,
        "output_summary": event.output_summary,
        "validation": event.validation,
        "error": event.error,
        "parent_event_id": event.parent_event_id,
    }


def _outbox_item(message: OutboxMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "tenant_id": message.tenant_id,
        "event_id": message.event.id,
        "event_type": message.event.type,
        "event_subject": message.event.subject,
        "event_data": _redacted_admin_payload(message.event.data),
        "idempotency_key": message.idempotency_key,
        "status": message.dispatch_status.value,
        "claim_owner": message.claim_owner,
        "claimed_until": _iso(message.claimed_until),
        "next_attempt_at": _iso(message.next_attempt_at),
        "attempts": message.attempts,
        "created_at": _iso(message.created_at),
        "published_at": _iso(message.published_at),
    }


def _scheduled_reminder_item(job: ScheduledReminder, *, now: datetime) -> dict[str, Any]:
    sent = bool(job.sent)
    due = _reminder_due(job, now)
    return {
        "reminder_id": job.reminder_id,
        "tenant_id": job.tenant_id,
        "calendar_event_id": job.calendar_event_id,
        "notify_at": _iso(job.notify_at),
        "channel": job.channel,
        "recipient": job.recipient,
        "body_preview": _preview(job.body),
        "idempotency_key": job.idempotency_key,
        "sent": sent,
        "due": due,
        "status": "sent" if sent else "due" if due else "scheduled",
    }


def _agenda_item(event: CalendarEventResult, *, now: datetime) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": event.title,
        "starts_at": _iso(event.starts_at),
        "idempotency_key": event.idempotency_key,
        "reused": event.reused,
        "status": "upcoming" if _is_on_or_after(event.starts_at, now) else "past",
    }


def _reminder_item(
    job: ScheduledReminder,
    *,
    now: datetime,
    event: CalendarEventResult | None,
) -> dict[str, Any]:
    item = _scheduled_reminder_item(job, now=now)
    item.update(
        {
            "event_title": event.title if event is not None else None,
            "event_starts_at": _iso(event.starts_at) if event is not None else None,
        }
    )
    return item


def _workflow_state_item(state: WorkflowState) -> dict[str, Any]:
    return {
        "workflow_id": state.workflow_id,
        "tenant_id": state.tenant_id,
        "workflow_type": state.workflow_type,
        "status": state.status.value,
        "step": state.step,
        "idempotency_key": state.idempotency_key,
        "data": _redacted_admin_payload(state.data),
        "created_at": _iso(state.created_at),
        "updated_at": _iso(state.updated_at),
    }


def _memory_item(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "kind": record.kind.value,
        "text_preview": _preview(record.text),
        "source": record.source,
        "confirmed": record.confirmed,
        "created_at": _iso(record.created_at),
    }


def _event_item(event: CloudEvent) -> dict[str, Any]:
    item = event.model_dump(mode="json")
    item["data"] = _redacted_admin_payload(event.data)
    return item


def _approval_item(approval: PendingApproval) -> dict[str, Any]:
    return {
        "approval_id": approval.approval_id,
        "action": approval.action,
        "resource": approval.resource,
        "status": _APPROVAL_STATUS_LABELS.get(approval.status, "pending"),
        "title": _preview(approval.request_text),
        "created_at": _iso(approval.created_at),
    }


_APPROVAL_STATUS_LABELS = {
    PendingApprovalStatus.pending: "pending",
    PendingApprovalStatus.approved: "approved",
    PendingApprovalStatus.cancelled: "rejected",
}
