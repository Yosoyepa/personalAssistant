"""Reminder use-case DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.domain.common.permissions import ApprovalGrant
from personal_assistant.domain.reminders.models import ReminderIntent


class ReminderWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: str = Field(
        min_length=1,
        description="Provider message reference retained for approval and conversation workflows.",
    )
    source_event_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Stable provider event identifier used by idempotency; message_id is the transitional fallback until "
            "transport adapters populate it explicitly."
        ),
    )
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: Literal["telegram", "whatsapp"] = "telegram"
    recipient: str = Field(min_length=1)
    now: datetime
    timezone: str = "America/Bogota"
    idempotency_key: str | None = Field(
        default=None,
        description="Transitional assertion only; the workflow always derives and verifies the v2 key.",
    )
    approval: ApprovalGrant | None = None

    @field_validator("now")
    @classmethod
    def require_aware_now(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value


class ReminderWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus
    intent: ReminderIntent
    reply: str
    idempotency_key: str
    approval_required: bool = False
    calendar_event_id: str | None = None
    reminder_id: str | None = None
    reused: bool = False
    trace_ids: list[str] = Field(default_factory=list)
