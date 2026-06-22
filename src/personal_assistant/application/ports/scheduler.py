"""Reminder scheduling application port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from personal_assistant.domain.common.identity import Principal


class ScheduledReminder(BaseModel):
    reminder_id: str = Field(default_factory=lambda: f"rem_{uuid4().hex}")
    tenant_id: str
    calendar_event_id: str
    notify_at: datetime
    channel: str
    recipient: str
    body: str
    idempotency_key: str
    sent: bool = False


class ReminderSchedulerPort(Protocol):
    def schedule_before_event(
        self,
        principal: Principal,
        *,
        calendar_event_id: str,
        starts_at: datetime,
        channel: str,
        recipient: str,
        body: str,
        minutes_before: int = 30,
        idempotency_key: str,
    ) -> ScheduledReminder:
        """Schedule an idempotent reminder before a calendar event."""
