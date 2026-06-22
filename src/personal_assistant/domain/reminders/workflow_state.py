"""Typed reminder workflow state helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any

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
    starts_at: datetime
    confidence: float = Field(ge=0, le=1)

    @field_validator("starts_at")
    @classmethod
    def require_aware_starts_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        return value

    @classmethod
    def from_extraction(cls, extraction: ReminderExtraction) -> "ReminderDraft":
        return cls(
            title=extraction.title,
            starts_at=extraction.starts_at,
            confidence=extraction.confidence,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ReminderDraft":
        return cls.model_validate(
            {
                "title": data["title"],
                "starts_at": data["starts_at"],
                "confidence": data.get("confidence", 0.86),
            }
        )

    def to_extraction(self) -> ReminderExtraction:
        return ReminderExtraction(title=self.title, starts_at=self.starts_at, confidence=self.confidence)

    def to_workflow_data(self) -> dict[str, str | float]:
        return {
            "title": self.title,
            "starts_at": self.starts_at.isoformat(),
            "confidence": self.confidence,
        }
