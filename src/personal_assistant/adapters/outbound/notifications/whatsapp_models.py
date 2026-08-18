"""Data models for WhatsApp Cloud API outbound notifications."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_assistant.application.ports.notifications import (
    NotificationOutcome,
)


class WhatsAppProviderResult(BaseModel):
    """Sanitized outcome returned by the WhatsApp transport boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: NotificationOutcome
    provider_code: int | None = Field(default=None, strict=True, ge=100, le=599)
    retry_after: int | None = Field(default=None, strict=True, gt=0)
    notification_id: str | None = None

    @model_validator(mode="after")
    def validate_metadata(self) -> WhatsAppProviderResult:
        if self.outcome == "success":
            if not self.notification_id:
                raise ValueError(
                    "WhatsApp success requires a confirmed notification id"
                )
            if self.provider_code is not None or self.retry_after is not None:
                raise ValueError("WhatsApp success cannot carry failure metadata")
            return self
        if self.notification_id is not None:
            raise ValueError(
                "WhatsApp failure cannot carry a confirmed notification id"
            )
        if self.retry_after is not None and self.outcome != "known-transient":
            raise ValueError("retry_after is only valid for known-transient outcomes")
        return self


class WhatsAppClient(Protocol):
    def send_message(self, *, recipient: str, text: str) -> WhatsAppProviderResult:
        """Send one WhatsApp text message and return provider metadata."""
