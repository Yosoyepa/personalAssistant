"""Notification application port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class NotificationRequest(BaseModel):
    channel: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1)
    send_at: datetime | None = None
    idempotency_key: str = Field(min_length=1)


class NotificationResult(BaseModel):
    notification_id: str
    channel: str
    recipient: str
    idempotency_key: str
    reused: bool = False


class NotificationPort(Protocol):
    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        """Send or reuse an approved idempotent notification."""
