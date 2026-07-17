"""CloudEvents-style application event DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_assistant.application.dto.delivery import DeliveryError, DeliveryStatus


class CloudEvent(BaseModel):
    """Internal event envelope.

    It follows the CloudEvents shape closely while keeping tenant and workflow
    correlation explicit for repository-level isolation and replay.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    specversion: str = "1.0"
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    subject: str | None = None
    tenant_id: str = Field(min_length=1)
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    causation_id: str | None = None
    source_event_id: str | None = Field(default=None, min_length=1)
    payload_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    timezone: str | None = Field(default=None, min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return ZoneInfo(value).key
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

    @field_validator("time")
    @classmethod
    def canonicalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event time must be timezone-aware")
        return value.astimezone(UTC)


OutboxStatus = DeliveryStatus


class OutboxMessage(BaseModel):
    """Message waiting to be published to an external channel/broker."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: f"out_{uuid4().hex}")
    tenant_id: str = Field(min_length=1)
    event: CloudEvent
    idempotency_key: str = Field(min_length=1)
    dispatch_status: OutboxStatus = OutboxStatus.pending
    claim_token: str | None = None
    claim_owner: str | None = None
    claimed_until: datetime | None = None
    next_attempt_at: datetime | None = None
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sending_at: datetime | None = None
    published_at: datetime | None = None
    last_error: DeliveryError | None = None

    @property
    def claimed(self) -> bool:
        return self.dispatch_status == OutboxStatus.claimed

    @property
    def published(self) -> bool:
        return self.dispatch_status == OutboxStatus.published

    @field_validator(
        "claimed_until",
        "next_attempt_at",
        "created_at",
        "sending_at",
        "published_at",
    )
    @classmethod
    def canonicalize_delivery_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("outbox delivery timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_state_metadata(self) -> OutboxMessage:
        status = self.dispatch_status
        if status == OutboxStatus.pending:
            if any(
                value is not None
                for value in (
                    self.claim_token,
                    self.claim_owner,
                    self.claimed_until,
                    self.sending_at,
                    self.published_at,
                )
            ):
                raise ValueError(
                    "pending outbox messages cannot retain claim or sending metadata"
                )
        elif status == OutboxStatus.claimed:
            if (
                not self.claim_token
                or not self.claim_owner
                or self.claimed_until is None
            ):
                raise ValueError(
                    "claimed outbox messages require token, owner, and lease"
                )
            if self.sending_at is not None or self.published_at is not None:
                raise ValueError(
                    "claimed outbox messages cannot have delivery timestamps"
                )
        elif status == OutboxStatus.sending:
            if (
                not self.claim_token
                or not self.claim_owner
                or self.claimed_until is None
                or self.sending_at is None
                or self.attempts < 1
            ):
                raise ValueError(
                    "sending outbox messages require claim metadata, sending_at, and attempt"
                )
            if self.published_at is not None:
                raise ValueError("sending outbox messages cannot have published_at")
        elif status == OutboxStatus.published:
            # Legacy binaries cleared the token and never wrote sending_at.
            # published_at is the only invariant shared across both formats.
            if self.published_at is None:
                raise ValueError("published outbox messages require published_at")
            if self.last_error is not None:
                raise ValueError("published outbox messages cannot retain last_error")
            if any(
                value is not None
                for value in (self.claim_token, self.claim_owner, self.claimed_until)
            ):
                raise ValueError("published outbox messages cannot retain a claim")
        elif status in {OutboxStatus.failed, OutboxStatus.uncertain}:
            if self.sending_at is None or self.last_error is None or self.attempts < 1:
                raise ValueError(
                    "failed or uncertain outbox messages require attempt, sending_at, and error"
                )
            if self.published_at is not None:
                raise ValueError(
                    "failed or uncertain messages cannot have published_at"
                )
            if any(
                value is not None
                for value in (self.claim_token, self.claim_owner, self.claimed_until)
            ):
                raise ValueError("terminal outbox messages cannot retain a claim")
        return self
