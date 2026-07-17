"""Durable delivery state shared by outbox and scheduled reminders."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CLAIM_LIMIT = 1000
MAX_CLAIM_LEASE_SECONDS = 86_400
MAX_CLAIM_OWNER_LENGTH = 200
CLAIM_OWNER_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}"


def is_valid_claim_owner(value: str) -> bool:
    """Return whether a normalized owner is safe for persistence and logs."""

    return re.fullmatch(CLAIM_OWNER_PATTERN, value) is not None


class DeliveryStatus(str, Enum):
    """Persisted delivery states.

    ``sending`` is the external-I/O boundary: it must be committed before the
    provider is called. ``failed`` and ``published`` are terminal, while an
    ``uncertain`` result requires explicit operator reconciliation.
    """

    pending = "pending"
    claimed = "claimed"
    sending = "sending"
    published = "published"
    failed = "failed"
    uncertain = "uncertain"


class DeliveryErrorCategory(str, Enum):
    """Low-cardinality, provider-independent failure categories."""

    network = "network"
    rate_limited = "rate_limited"
    rejected = "rejected"
    configuration = "configuration"
    internal = "internal"
    unknown = "unknown"


class DeliveryErrorCode(str, Enum):
    """Closed, non-secret reasons suitable for persistence and metrics."""

    timeout = "timeout"
    connection_failed = "connection_failed"
    rate_limited = "rate_limited"
    provider_unavailable = "provider_unavailable"
    authentication_failed = "authentication_failed"
    request_rejected = "request_rejected"
    invalid_configuration = "invalid_configuration"
    internal_error = "internal_error"
    unknown = "unknown"


class DeliveryError(BaseModel):
    """Sanitized failure metadata safe to persist and expose operationally.

    Free-form provider messages are deliberately absent. ``code`` is a short,
    normalized identifier and cannot contain URLs, tokens, recipients or body
    text.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: DeliveryErrorCategory
    code: DeliveryErrorCode
    provider_code: int | None = Field(default=None, ge=0, le=9999)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def canonicalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)


def canonical_utc(value: datetime, *, field: str) -> datetime:
    """Validate and normalize a caller-supplied transition timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)
