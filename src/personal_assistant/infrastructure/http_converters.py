"""Payload conversion and mapping helpers for reminders and approvals."""

from __future__ import annotations

import hashlib
from typing import Literal, cast

from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
)
from personal_assistant.application.dto.reminders import (
    ReminderWorkflowInput,
    ReminderWorkflowResult,
)
from personal_assistant.application.dto.runtime import ApprovalStatus
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.http_models import (
    ApprovalView,
    ReminderCommandRequest,
    ReminderCommandResponse,
)


def _effective_idempotency_key(
    principal: Principal, request: ReminderCommandRequest
) -> str:
    return reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel=request.channel,
        principal_id=principal.principal_id,
        conversation_id=request.conversation_id,
        source_event_id=request.source_event_id,
    )


def _approval_id(
    tenant_id: str, principal_id: str, idempotency_key: str, action: str
) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}:{principal_id}:{idempotency_key}:{action}".encode()
    ).hexdigest()[:24]
    return f"apr_{digest}"


def _approval_status_from_pending(status: PendingApprovalStatus) -> ApprovalStatus:
    if status == PendingApprovalStatus.approved:
        return ApprovalStatus.approved
    if status == PendingApprovalStatus.cancelled:
        return ApprovalStatus.rejected
    return ApprovalStatus.pending


def _pending_status_from_approval(
    status: ApprovalStatus,
) -> PendingApprovalStatus | None:
    if status == ApprovalStatus.approved:
        return PendingApprovalStatus.approved
    if status == ApprovalStatus.rejected:
        return PendingApprovalStatus.cancelled
    if status == ApprovalStatus.pending:
        return PendingApprovalStatus.pending
    return None


def _approval_view_from_pending(
    pending: PendingApproval, *, reason: str
) -> ApprovalView:
    return ApprovalView(
        approval_id=pending.approval_id,
        action=pending.action,
        resource=pending.resource,
        permission_tier=PermissionTier(pending.tier),
        reason=reason,
        status=_approval_status_from_pending(pending.status),
        created_at=pending.created_at,
    )


def _pending_approval_from_request(
    *,
    principal: Principal,
    request: ReminderCommandRequest,
    run_id: str,
    payload_fingerprint: str,
    action: str,
) -> PendingApproval:
    return PendingApproval(
        approval_id=_approval_id(
            principal.tenant_id, principal.principal_id, run_id, action
        ),
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        action=action,
        resource=f"{run_id}:calendar",
        tier=PermissionTier.P3.value,
        workflow_kind="reminder.create",
        message_id=request.message_id,
        source_event_id=request.source_event_id,
        conversation_id=request.conversation_id,
        channel=request.channel,
        recipient=request.recipient,
        request_text=request.text,
        request_now=request.now,
        timezone=request.timezone,
        idempotency_key=run_id,
        payload_fingerprint=payload_fingerprint,
    )


def _workflow_input_from_pending(
    pending: PendingApproval,
    *,
    approval: ApprovalGrant,
) -> ReminderWorkflowInput:
    return ReminderWorkflowInput(
        message_id=pending.message_id,
        source_event_id=pending.source_event_id,
        conversation_id=pending.conversation_id,
        text=pending.request_text,
        channel=cast(Literal["telegram", "whatsapp"], pending.channel),
        recipient=pending.recipient,
        now=pending.request_now,
        timezone=pending.timezone,
        idempotency_key=pending.idempotency_key,
        approval=approval,
    )


def _reminder_response(
    *,
    principal: Principal,
    run_id: str,
    result: ReminderWorkflowResult,
    approval: ApprovalView | None = None,
) -> ReminderCommandResponse:
    return ReminderCommandResponse(
        run_id=run_id,
        tenant_id=principal.tenant_id,
        status=result.status,
        intent=result.intent.value,
        reply=result.reply,
        source_event_id=result.source_event_id,
        payload_fingerprint=result.payload_fingerprint,
        timezone=result.timezone,
        clarification_reason=(
            result.clarification_reason.value
            if result.clarification_reason is not None
            else None
        ),
        clarification_reply_id=result.clarification_reply_id,
        clarification_reply_version=result.clarification_reply_version,
        approval_required=result.approval_required,
        approval=approval,
        calendar_event_id=result.calendar_event_id,
        reminder_id=result.reminder_id,
        reused=result.reused,
        trace_ids=result.trace_ids,
    )
