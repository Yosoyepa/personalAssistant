"""Reminder use-case DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.domain.common.permissions import ApprovalGrant
from personal_assistant.domain.reminders.models import (
    ReminderClarificationReason,
    ReminderIntent,
)


class ReminderWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: str = Field(
        min_length=1,
        description="Provider message reference retained for approval and conversation workflows.",
    )
    source_event_id: str = Field(
        min_length=1,
        description=(
            "Stable provider event identifier used to derive the v2 idempotency identity."
        ),
    )
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: Literal["telegram", "whatsapp"] = "telegram"
    recipient: str = Field(min_length=1)
    now: datetime
    timezone: str = Field(
        default="America/Bogota",
        min_length=1,
        description=(
            "Requested timezone. The reminder parser validates it so invalid user input "
            "can produce a typed clarification instead of transport validation failure."
        ),
    )
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
        return value.astimezone(UTC)


class ReminderWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus
    intent: ReminderIntent
    reply: str
    idempotency_key: str
    source_event_id: str = Field(min_length=1)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    timezone: str = Field(
        min_length=1,
        description=(
            "Canonical IANA timezone for effecting results; requested value for an "
            "invalid_timezone clarification."
        ),
    )
    clarification_reason: ReminderClarificationReason | None = None
    clarification_reply_id: str | None = Field(default=None, min_length=1)
    clarification_reply_version: str | None = Field(
        default=None, pattern=r"^v[1-9][0-9]*$"
    )
    approval_required: bool = False
    calendar_event_id: str | None = None
    reminder_id: str | None = None
    reused: bool = False
    trace_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_versioned_clarification_identity(self) -> "ReminderWorkflowResult":
        if self.clarification_reason != ReminderClarificationReason.invalid_timezone:
            try:
                self.timezone = ZoneInfo(self.timezone).key
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError(
                    "timezone must be valid unless requesting timezone clarification"
                ) from exc
        if self.clarification_reason is None:
            if (
                self.clarification_reply_id is not None
                or self.clarification_reply_version is not None
            ):
                raise ValueError("clarification reply identity requires a reason")
            return self
        expected_reply_id = f"reminder_{self.clarification_reason.value}"
        if self.clarification_reply_id != expected_reply_id:
            raise ValueError("clarification reply id must match its typed reason")
        if self.clarification_reply_version is None:
            raise ValueError("clarification reply version is required")
        return self
