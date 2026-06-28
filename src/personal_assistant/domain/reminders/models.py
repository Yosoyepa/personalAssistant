"""Reminder domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from personal_assistant.domain.common.base import DomainModel


class ReminderIntent(str, Enum):
    create = "reminder.create"
    unsupported = "unsupported"


class ReminderExtraction(DomainModel):
    title: str = Field(min_length=1)
    starts_at: datetime
    notify_at: datetime | None = None
    confidence: float = Field(ge=0, le=1)

    @field_validator("starts_at", "notify_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reminder datetimes must be timezone-aware")
        return value
