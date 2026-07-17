"""Reminder domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from personal_assistant.domain.common.base import DomainModel


class ReminderIntent(str, Enum):
    create = "reminder.create"
    unsupported = "unsupported"


class ReminderClarificationReason(str, Enum):
    ambiguous_hour = "ambiguous_hour"
    missing_date = "missing_date"
    missing_time = "missing_time"
    missing_datetime = "missing_datetime"
    nonexistent_local_time = "nonexistent_local_time"
    ambiguous_local_time = "ambiguous_local_time"
    invalid_timezone = "invalid_timezone"


class ReminderUnsupportedReason(str, Enum):
    not_a_reminder = "not_a_reminder"
    invalid_reference_instant = "invalid_reference_instant"
    invalid_relative_amount = "invalid_relative_amount"
    invalid_time = "invalid_time"
    past_time = "past_time"
    conflicting_temporal_expression = "conflicting_temporal_expression"


class ReminderExtraction(DomainModel):
    title: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    starts_at: datetime
    notify_at: datetime | None = None
    confidence: float = Field(ge=0, le=1)

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            zone = ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return zone.key

    @field_validator("starts_at", "notify_at")
    @classmethod
    def canonicalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reminder datetimes must be timezone-aware")
        return value.astimezone(UTC)


class ParsedReminder(DomainModel):
    status: Literal["parsed"] = "parsed"
    extraction: ReminderExtraction


class ReminderNeedsClarification(DomainModel):
    status: Literal["needs_clarification"] = "needs_clarification"
    reason: ReminderClarificationReason
    timezone: str | None = None


class UnsupportedReminder(DomainModel):
    status: Literal["unsupported"] = "unsupported"
    reason: ReminderUnsupportedReason


ReminderParseResult: TypeAlias = Annotated[
    ParsedReminder | ReminderNeedsClarification | UnsupportedReminder,
    Field(discriminator="status"),
]
