"""Event store and outbox application ports."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.domain.common.events import CloudEvent, OutboxMessage
from personal_assistant.domain.common.identity import Principal


class EventStorePort(Protocol):
    def append(self, principal: Principal, event: CloudEvent) -> CloudEvent:
        """Persist a tenant-scoped event idempotently."""


class OutboxPort(Protocol):
    def add(self, principal: Principal, event: CloudEvent, *, idempotency_key: str) -> OutboxMessage:
        """Add an event to the transactional outbox idempotently."""
