"""Event store and outbox application ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from personal_assistant.application.dto.events import CloudEvent, OutboxMessage
from personal_assistant.domain.common.identity import Principal


class EventStorePort(Protocol):
    def append(self, principal: Principal, event: CloudEvent) -> CloudEvent:
        """Persist a tenant-scoped event idempotently."""

    def list_for_tenant(self, principal: Principal) -> list[CloudEvent]:
        """List events visible to the authenticated tenant."""


class OutboxPort(Protocol):
    def add(
        self,
        principal: Principal,
        event: CloudEvent,
        *,
        idempotency_key: str,
        next_attempt_at: datetime | None = None,
        message_id: str | None = None,
    ) -> OutboxMessage:
        """Add an event to the outbox with an optional activation time and ID."""

    def claim(
        self,
        principal: Principal,
        limit: int = 10,
        *,
        owner: str = "local-worker",
        lease_seconds: int = 60,
    ) -> list[OutboxMessage]:
        """Claim pending or expired messages for one tenant."""

    def mark_published(
        self, principal: Principal, message_id: str, *, claim_token: str
    ) -> OutboxMessage:
        """Mark a claimed outbox message as published."""

    def release(
        self, principal: Principal, message_id: str, *, claim_token: str
    ) -> OutboxMessage:
        """Release a claimed outbox message back to pending."""

    def list_for_tenant(self, principal: Principal) -> list[OutboxMessage]:
        """List outbox messages visible to the authenticated tenant."""
