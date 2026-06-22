"""Memory domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import Field

from personal_assistant.domain.common.base import DomainModel


class MemoryKind(str, Enum):
    episodic = "episodic"
    semantic = "semantic"
    procedural = "procedural"


class MemoryRecord(DomainModel):
    id: str = Field(default_factory=lambda: f"mem_{uuid4().hex}")
    tenant_id: str
    user_id: str | None = None
    kind: MemoryKind
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confirmed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
