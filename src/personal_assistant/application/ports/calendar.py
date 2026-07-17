"""Calendar application port."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class CalendarEventRequest(BaseModel):
    event_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime | None = None
    timezone: str = "America/Bogota"
    idempotency_key: str = Field(min_length=1)
    source_event_id: str | None = Field(default=None, min_length=1)
    payload_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            return ZoneInfo(value).key
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

    @field_validator("starts_at", "ends_at")
    @classmethod
    def canonicalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calendar datetimes must be timezone-aware")
        return value.astimezone(UTC)


class CalendarEventResult(BaseModel):
    event_id: str
    title: str
    starts_at: datetime
    timezone: str
    idempotency_key: str
    source_event_id: str | None = Field(default=None, min_length=1)
    payload_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reused: bool = False

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            return ZoneInfo(value).key
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

    @field_validator("starts_at")
    @classmethod
    def canonicalize_starts_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        return value.astimezone(UTC)


class CalendarPort(Protocol):
    def create_event(
        self,
        principal: Principal,
        request: CalendarEventRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> CalendarEventResult:
        """Create or reuse an idempotent calendar event."""

    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        """List tenant-scoped calendar events for the authenticated principal."""


class CalendarReadPort(Protocol):
    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        """List tenant-scoped calendar events for read-only surfaces."""
