"""Normalized channel message models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ChannelName(str, Enum):
    telegram = "telegram"
    whatsapp = "whatsapp"


class NormalizedMessage(BaseModel):
    channel: ChannelName
    actor_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    source_event_id: str = Field(
        min_length=1,
        description=(
            "Stable provider delivery/event identifier; distinct from the referenced message."
        ),
    )
    text: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)
    command: str | None = Field(default=None, min_length=1)
    command_args: str = ""
    media_kind: str | None = Field(default=None, min_length=1)
    media_file_id: str | None = Field(default=None, min_length=1)
    media_mime_type: str | None = Field(default=None, min_length=1)
    media_file_size: int | None = Field(default=None, ge=0)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OutboundMessage(BaseModel):
    channel: ChannelName
    recipient: str = Field(min_length=1)
    text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
