"""DTOs for channel command handling."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

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
    source_event_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    request_text: str = Field(min_length=1)
    request_now: datetime = Field(default_factory=lambda: datetime.now(UTC))
    timezone: str = "America/Bogota"
    idempotency_key: str = Field(min_length=1)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PendingApprovalStatus = PendingApprovalStatus.pending
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            return ZoneInfo(value).key
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

    @field_validator("request_now")
    @classmethod
    def canonicalize_request_now(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("request_now must be timezone-aware")
        return value.astimezone(UTC)


class InferredCommandIntent(ApplicationDTO):
    kind: CommandKind
    confidence: float = Field(ge=0.0, le=1.0)
    reminder_text: str | None = Field(default=None, min_length=1, max_length=500)
