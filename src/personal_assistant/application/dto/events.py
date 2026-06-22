"""CloudEvents-style application event DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


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
    data: dict[str, Any] = Field(default_factory=dict)
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OutboxStatus(str, Enum):
    pending = "pending"
    claimed = "claimed"
    published = "published"
    failed = "failed"


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
    published_at: datetime | None = None

    @property
    def claimed(self) -> bool:
        return self.dispatch_status == OutboxStatus.claimed

    @property
    def published(self) -> bool:
        return self.dispatch_status == OutboxStatus.published
