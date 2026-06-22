"""DTOs for channel command handling."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import Field

from personal_assistant.application.dto.base import ApplicationDTO
from personal_assistant.application.dto.runtime import AgentStatus


class CommandKind(str, Enum):
    start = "start"
    help = "help"
    status = "status"
    reminder_create = "reminder.create"
    agenda = "agenda"
    pending_approvals = "pending_approvals"
    approve = "approve"
    cancel = "cancel"
    unsupported = "unsupported"


class CommandResult(ApplicationDTO):
    status: AgentStatus
    kind: CommandKind
    reply: str = Field(min_length=1)
    approval_id: str | None = None
    dispatch_required: bool = True
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class PendingApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    cancelled = "cancelled"


class PendingApproval(ApplicationDTO):
    approval_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    tier: str = Field(min_length=2)
    workflow_kind: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    request_text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: PendingApprovalStatus = PendingApprovalStatus.pending
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
