"""Reminder scheduling application port."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from personal_assistant.domain.common.identity import Principal


class ScheduledReminder(BaseModel):
    reminder_id: str = Field(default_factory=lambda: f"rem_{uuid4().hex}")
    tenant_id: str
    calendar_event_id: str
    notify_at: datetime
    timezone: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel: str
    recipient: str
    body: str
    idempotency_key: str
    sent: bool = False

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            return ZoneInfo(value).key
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

    @field_validator("notify_at")
    @classmethod
    def canonicalize_notify_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("notify_at must be timezone-aware")
        return value.astimezone(UTC)


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
        timezone: str,
        source_event_id: str,
        payload_fingerprint: str,
        minutes_before: int = 30,
        idempotency_key: str,
    ) -> ScheduledReminder:
        """Schedule an idempotent reminder before a calendar event."""


class ReminderSchedulerWorkerPort(ReminderSchedulerPort, Protocol):
    def due(self, principal: Principal, now: datetime) -> list[ScheduledReminder]:
        """Return unsent reminders due for the authenticated tenant."""

    def mark_sent(self, principal: Principal, reminder_id: str) -> ScheduledReminder:
        """Mark one reminder as sent for the authenticated tenant."""

    def list_for_tenant(self, principal: Principal) -> list[ScheduledReminder]:
        """List scheduled reminders visible to the authenticated tenant."""
