"""Notification application port."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class NotificationMedia(BaseModel):
    kind: Literal["audio"] = "audio"
    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    data: bytes = Field(min_length=1)


class NotificationRequest(BaseModel):
    channel: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1)
    send_at: datetime | None = None
    idempotency_key: str = Field(min_length=1)
    media: NotificationMedia | None = None


NotificationOutcome = Literal[
    "success",
    "known-transient",
    "permanent",
    "unknown-outcome",
]


class NotificationResult(BaseModel):
    """Typed delivery result without message content or recipient metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    notification_id: str | None = None
    channel: str
    idempotency_key: str
    outcome: NotificationOutcome = "success"
    provider_code: int | None = Field(default=None, strict=True, ge=100, le=599)
    retry_after: int | None = Field(default=None, strict=True, gt=0)
    provider_message_id: int | None = Field(default=None, strict=True, gt=0)
    reused: bool = False

    @model_validator(mode="after")
    def validate_outcome_metadata(self) -> NotificationResult:
        if self.outcome == "success":
            if self.notification_id is None:
                raise ValueError("successful notification requires notification_id")
            if self.provider_code is not None or self.retry_after is not None:
                raise ValueError(
                    "successful notification cannot carry failure metadata"
                )
            return self

        if self.notification_id is not None or self.provider_message_id is not None:
            raise ValueError(
                "failed notification cannot carry confirmed delivery metadata"
            )
        if self.retry_after is not None and self.outcome != "known-transient":
            raise ValueError("retry_after is only valid for known-transient outcomes")
        return self


class NotificationPort(Protocol):
    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        """Send or reuse an approved idempotent notification."""
