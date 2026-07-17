"""Typed reminder workflow state helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from personal_assistant.domain.common.base import DomainModel
from personal_assistant.domain.reminders.models import ReminderExtraction


class ReminderWorkflowStep(str, Enum):
    classify = "classify"
    needs_clarification = "needs_clarification"
    approval_required = "approval_required"
    completed = "completed"


class ReminderDraft(DomainModel):
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

    @classmethod
    def from_extraction(cls, extraction: ReminderExtraction) -> "ReminderDraft":
        return cls(
            title=extraction.title,
            timezone=extraction.timezone,
            starts_at=extraction.starts_at,
            notify_at=extraction.notify_at,
            confidence=extraction.confidence,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ReminderDraft":
        return cls.model_validate(
            {
                "title": data["title"],
                "timezone": data.get("timezone", "UTC"),
                "starts_at": data["starts_at"],
                "notify_at": data.get("notify_at"),
                "confidence": data.get("confidence", 0.86),
            }
        )

    def to_extraction(self) -> ReminderExtraction:
        return ReminderExtraction(
            title=self.title,
            timezone=self.timezone,
            starts_at=self.starts_at,
            notify_at=self.notify_at,
            confidence=self.confidence,
        )

    def to_workflow_data(self) -> dict[str, str | float | None]:
        return {
            "title": self.title,
            "timezone": self.timezone,
            "starts_at": self.starts_at.isoformat(),
            "notify_at": self.notify_at.isoformat()
            if self.notify_at is not None
            else None,
            "confidence": self.confidence,
        }
