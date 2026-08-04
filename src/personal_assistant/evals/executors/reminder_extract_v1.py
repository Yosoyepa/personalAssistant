"""Deterministic executor for reminder temporal extraction."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from personal_assistant.domain.reminders.models import ParsedReminder
from personal_assistant.domain.reminders.parser import extract_reminder
from personal_assistant.evals.schema import StrictModel


class InputModel(StrictModel):
    text: str = Field(min_length=1)
    now: datetime
    timezone: str = Field(min_length=1)

    @model_validator(mode="after")
    def aware_reference(self) -> InputModel:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must include a UTC offset")
        return self


class ExpectedModel(StrictModel):
    status: Literal["parsed", "needs_clarification", "unsupported"]
    reason: str | None
    timezone: str | None
    startsAt: datetime | None
    notifyAt: datetime | None

    @model_validator(mode="after")
    def valid_shape(self) -> ExpectedModel:
        if self.status == "parsed":
            if self.startsAt is None or self.timezone is None:
                raise ValueError("parsed results require startsAt and timezone")
            if self.reason is not None:
                raise ValueError("parsed results forbid reason")
        elif self.reason is None:
            raise ValueError("non-parsed results require reason")
        return self


def execute(value: InputModel) -> dict[str, object]:
    result = extract_reminder(value.text, value.now, timezone=value.timezone)
    actual: dict[str, object] = {
        "status": result.status,
        "reason": None,
        "timezone": None,
        "startsAt": None,
        "notifyAt": None,
    }
    if isinstance(result, ParsedReminder):
        actual.update(
            timezone=result.extraction.timezone,
            startsAt=result.extraction.starts_at.isoformat().replace("+00:00", "Z"),
            notifyAt=(
                result.extraction.notify_at.isoformat().replace("+00:00", "Z")
                if result.extraction.notify_at is not None
                else None
            ),
        )
    else:
        actual["reason"] = result.reason.value
        if hasattr(result, "timezone"):
            actual["timezone"] = result.timezone
    return actual
