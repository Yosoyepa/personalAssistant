"""Reminder scheduling application port."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from personal_assistant.application.dto.delivery import DeliveryError, DeliveryStatus
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
    delivery_status: DeliveryStatus = DeliveryStatus.pending
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    sending_at: datetime | None = None
    published_at: datetime | None = None
    last_error: DeliveryError | None = None
    # Rollback guard for old binaries whose due query reads only sent=false.
    # It is false only for pending; it is not delivery truth. New code treats
    # delivery_status as canonical.
    sent: bool = False

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_sent(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        upgraded = dict(value)
        if "delivery_status" not in upgraded:
            upgraded["delivery_status"] = (
                DeliveryStatus.published
                if upgraded.get("sent") is True
                else DeliveryStatus.pending
            )
        upgraded["sent"] = upgraded["delivery_status"] not in {
            DeliveryStatus.pending,
            DeliveryStatus.pending.value,
        }
        return upgraded

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

    @field_validator("next_attempt_at", "sending_at", "published_at")
    @classmethod
    def canonicalize_delivery_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("delivery timestamps must be timezone-aware")
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
        reminder_id: str | None = None,
    ) -> ScheduledReminder:
        """Schedule an idempotent reminder before a calendar event."""


class ReminderSchedulerWorkerPort(ReminderSchedulerPort, Protocol):
    def due(self, principal: Principal, now: datetime) -> list[ScheduledReminder]:
        """Return unsent reminders due for the authenticated tenant."""

    def mark_sent(self, principal: Principal, reminder_id: str) -> ScheduledReminder:
        """Mark one reminder as sent for the authenticated tenant."""

    def list_for_tenant(self, principal: Principal) -> list[ScheduledReminder]:
        """List scheduled reminders visible to the authenticated tenant."""
