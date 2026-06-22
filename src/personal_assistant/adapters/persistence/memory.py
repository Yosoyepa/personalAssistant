"""Minimal tenant-scoped memory implementation."""

from __future__ import annotations

from personal_assistant.domain.common.identity import Principal, require_trusted_principal
from personal_assistant.domain.memory.models import MemoryKind, MemoryRecord


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
