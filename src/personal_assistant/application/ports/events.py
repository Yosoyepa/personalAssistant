"""Event store and outbox application ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from personal_assistant.application.dto.delivery import DeliveryError
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

    def claim_due(
        self,
        principal: Principal,
        now: datetime,
        *,
        limit: int = 10,
        owner: str = "local-worker",
        lease_seconds: int = 60,
        event_type: str | None = None,
    ) -> list[OutboxMessage]:
        """Claim due work with bounded limit, owner length, and lease duration."""

    def sweep_expired_sending(
        self,
        principal: Principal,
        now: datetime,
        *,
        error: DeliveryError,
        limit: int = 10,
        event_type: str | None = None,
    ) -> list[OutboxMessage]:
        """Atomically fence expired sending rows into uncertain without resend."""

    def mark_sending(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        started_at: datetime,
    ) -> OutboxMessage:
        """Persist the external-I/O boundary for a currently claimed message."""

    def mark_claim_failed(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        error: DeliveryError,
    ) -> OutboxMessage:
        """Fail invalid claimed work before I/O without incrementing attempts."""

    def mark_published(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        published_at: datetime,
    ) -> OutboxMessage:
        """Mark sending as published and clear its claim metadata."""

    def mark_failed(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        error: DeliveryError,
    ) -> OutboxMessage:
        """Record a known terminal failure from sending."""

    def mark_uncertain(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        error: DeliveryError,
    ) -> OutboxMessage:
        """Record ambiguous provider outcome requiring explicit reconciliation."""

    def release(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        next_attempt_at: datetime | None = None,
    ) -> OutboxMessage:
        """Release claimed work before external I/O; never releases sending."""

    def reschedule(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        next_attempt_at: datetime,
        error: DeliveryError,
    ) -> OutboxMessage:
        """Move a known transient sending result back to pending."""

    def resolve_uncertain_delivered(
        self,
        principal: Principal,
        message_id: str,
        *,
        published_at: datetime,
    ) -> OutboxMessage:
        """Reconcile an uncertain message as delivered after operator confirmation."""

    def resolve_uncertain_retry(
        self,
        principal: Principal,
        message_id: str,
        *,
        next_attempt_at: datetime,
    ) -> OutboxMessage:
        """Reconcile an uncertain message back to pending after confirmation."""

    def list_for_tenant(self, principal: Principal) -> list[OutboxMessage]:
        """List outbox messages visible to the authenticated tenant."""
