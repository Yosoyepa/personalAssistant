"""Canonical reminder idempotency identity and payload contracts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from pydantic import ConfigDict, field_validator

from personal_assistant.domain.common.base import DomainModel
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode


REMINDER_IDEMPOTENCY_VERSION = 2
REMINDER_IDEMPOTENCY_KEY_PREFIX = "reminder:v2:"
_IDENTITY_SCHEMA = "personal-assistant.reminder-idempotency-identity"
_PAYLOAD_SCHEMA = "personal-assistant.reminder-idempotency-payload"

__all__ = [
    "REMINDER_IDEMPOTENCY_KEY_PREFIX",
    "REMINDER_IDEMPOTENCY_VERSION",
    "ReminderIdempotency",
    "ReminderIdempotencyConflict",
    "ReminderIdempotencyIdentity",
    "ReminderPayload",
    "ReminderReplayConflict",
    "reminder_idempotency_key",
]


def _canonical_json(value: dict[str, Any]) -> str:
    """Serialize a versioned document without delimiter ambiguity."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    """Trim boundaries and normalize Unicode while preserving case/content."""

    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise ValueError("idempotency value cannot be blank")
    return normalized


class ReminderIdempotencyIdentity(DomainModel):
    """Stable source-event identity used only to derive the v2 key.

    Opaque identifiers are case-sensitive. All fields are trimmed and NFC
    normalized; only the channel vocabulary is case-folded.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    channel: str
    principal_id: str
    conversation_id: str
    source_event_id: str

    @field_validator(
        "tenant_id", "principal_id", "conversation_id", "source_event_id", mode="before"
    )
    @classmethod
    def normalize_opaque_identifier(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("idempotency identifiers must be strings")
        return _normalize_text(value)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("idempotency channel must be a string")
        return _normalize_text(value).casefold()

    def canonical_document(self) -> dict[str, str | int]:
        return {
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "principal_id": self.principal_id,
            "schema": _IDENTITY_SCHEMA,
            "source_event_id": self.source_event_id,
            "tenant_id": self.tenant_id,
            "version": REMINDER_IDEMPOTENCY_VERSION,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_document())

    @property
    def idempotency_key(self) -> str:
        return f"{REMINDER_IDEMPOTENCY_KEY_PREFIX}{_sha256(self.canonical_json())}"


class ReminderPayload(DomainModel):
    """Canonical, effect-relevant payload fingerprinted separately from identity.

    Approval grants, caller-supplied keys, and processing time are deliberately
    excluded because they are replay control/context rather than source payload.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    recipient: str
    timezone: str

    @field_validator("text", "recipient", "timezone", mode="before")
    @classmethod
    def normalize_payload_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("reminder payload values must be strings")
        return _normalize_text(value)

    def canonical_document(self) -> dict[str, str | int]:
        return {
            "recipient": self.recipient,
            "schema": _PAYLOAD_SCHEMA,
            "text": self.text,
            "timezone": self.timezone,
            "version": REMINDER_IDEMPOTENCY_VERSION,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_document())

    @property
    def fingerprint(self) -> str:
        return _sha256(self.canonical_json())


class ReminderIdempotency(DomainModel):
    """Pair the stable event identity with its independently hashed payload."""

    model_config = ConfigDict(frozen=True)

    identity: ReminderIdempotencyIdentity
    payload: ReminderPayload

    @property
    def key(self) -> str:
        return self.identity.idempotency_key

    @property
    def payload_fingerprint(self) -> str:
        return self.payload.fingerprint


class ReminderIdempotencyConflict(AssistantError):
    """The same canonical event identity was observed with a changed payload."""

    def __init__(self, *, tenant_id: str, idempotency_key: str) -> None:
        super().__init__(
            ErrorCode.CONFLICT,
            "reminder replay payload conflicts with the registered event",
            context={
                "idempotency_key": idempotency_key,
                "identity_version": REMINDER_IDEMPOTENCY_VERSION,
            },
            tenant_id=tenant_id,
            retryable=False,
        )


# Readable alias for transport/application integration code.
ReminderReplayConflict = ReminderIdempotencyConflict


def reminder_idempotency_key(
    *,
    tenant_id: str,
    channel: str,
    principal_id: str,
    conversation_id: str,
    source_event_id: str,
) -> str:
    """Derive a v2 key only when the complete canonical identity is present."""

    return ReminderIdempotencyIdentity(
        tenant_id=tenant_id,
        channel=channel,
        principal_id=principal_id,
        conversation_id=conversation_id,
        source_event_id=source_event_id,
    ).idempotency_key
