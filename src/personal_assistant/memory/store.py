"""Minimal tenant-scoped memory implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from personal_assistant.shared.schemas import Principal, require_trusted_principal


class MemoryKind(str, Enum):
    episodic = "episodic"
    semantic = "semantic"
    procedural = "procedural"


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"mem_{uuid4().hex}")
    tenant_id: str
    user_id: str | None = None
    kind: MemoryKind
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confirmed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TenantMemoryStore:
    """Small in-memory store with hard tenant scoping at every operation."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def add(
        self,
        principal: Principal,
        *,
        kind: MemoryKind,
        text: str,
        source: str,
        confirmed: bool = False,
    ) -> MemoryRecord:
        require_trusted_principal(principal)
        record = MemoryRecord(
            tenant_id=principal.tenant_id,
            user_id=principal.actor_id,
            kind=kind,
            text=text,
            source=source,
            confirmed=confirmed,
        )
        self._records[record.id] = record
        return record

    def retrieve(
        self,
        principal: Principal,
        *,
        query: str,
        kind: MemoryKind | None = None,
        confirmed_only: bool = True,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        require_trusted_principal(principal)
        normalized = query.casefold()
        matches: list[MemoryRecord] = []
        for record in self._records.values():
            if record.tenant_id != principal.tenant_id:
                continue
            if record.user_id != principal.actor_id:
                continue
            if kind is not None and record.kind != kind:
                continue
            if confirmed_only and not record.confirmed:
                continue
            if normalized and normalized not in record.text.casefold():
                continue
            matches.append(record)
        return matches[:limit]

    def list_for_tenant(self, principal: Principal) -> list[MemoryRecord]:
        require_trusted_principal(principal)
        return [
            record
            for record in self._records.values()
            if record.tenant_id == principal.tenant_id and record.user_id == principal.actor_id
        ]
