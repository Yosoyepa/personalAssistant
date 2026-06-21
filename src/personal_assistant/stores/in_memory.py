"""In-memory stores with tenant-scoped access and idempotency."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from personal_assistant.shared.durable import WorkflowState, WorkflowStatus
from personal_assistant.shared.events import CloudEvent, OutboxMessage, OutboxStatus
from personal_assistant.shared.errors import AssistantError, ErrorCode
from personal_assistant.shared.schemas import Principal, require_trusted_principal


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events_by_key: dict[tuple[str, str], CloudEvent] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}

    def append(self, principal: Principal, event: CloudEvent) -> CloudEvent:
        require_trusted_principal(principal)
        if event.tenant_id != principal.tenant_id:
            raise AssistantError(ErrorCode.PERMISSION_DENIED, "event tenant mismatch", tenant_id=principal.tenant_id)
        key = (principal.tenant_id, event.id)
        event_fingerprint = _fingerprint(event.model_dump(mode="json"))
        existing = self._events_by_key.get(key)
        if existing is not None:
            if self._fingerprints[key] != event_fingerprint:
                raise AssistantError(ErrorCode.CONFLICT, "event idempotency conflict", tenant_id=principal.tenant_id)
            return existing
        self._events_by_key[key] = event
        self._fingerprints[key] = event_fingerprint
        return event

    def list_for_tenant(self, principal: Principal) -> list[CloudEvent]:
        require_trusted_principal(principal)
        return [event for (tenant_id, _), event in self._events_by_key.items() if tenant_id == principal.tenant_id]


class InMemoryOutbox:
    def __init__(self) -> None:
        self._messages_by_key: dict[tuple[str, str], OutboxMessage] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}

    def add(self, principal: Principal, event: CloudEvent, *, idempotency_key: str) -> OutboxMessage:
        require_trusted_principal(principal)
        if event.tenant_id != principal.tenant_id:
            raise AssistantError(ErrorCode.PERMISSION_DENIED, "outbox tenant mismatch", tenant_id=principal.tenant_id)
        key = (principal.tenant_id, idempotency_key)
        existing = self._messages_by_key.get(key)
        event_fingerprint = _fingerprint(event.model_dump(mode="json"))
        if existing is not None:
            if self._fingerprints[key] != event_fingerprint:
                raise AssistantError(ErrorCode.CONFLICT, "outbox idempotency conflict", tenant_id=principal.tenant_id)
            return existing
        message = OutboxMessage(
            tenant_id=principal.tenant_id,
            event=event,
            idempotency_key=idempotency_key,
        )
        self._messages_by_key[key] = message
        self._fingerprints[key] = event_fingerprint
        return message

    def claim(self, principal: Principal, limit: int = 10, *, owner: str = "local-worker", lease_seconds: int = 60) -> list[OutboxMessage]:
        require_trusted_principal(principal)
        now = datetime.now(UTC)
        claimed: list[OutboxMessage] = []
        for key, message in list(self._messages_by_key.items()):
            if key[0] != principal.tenant_id or message.published:
                continue
            if message.claimed_until is not None and message.claimed_until > now:
                continue
            updated = message.model_copy(
                update={
                    "dispatch_status": OutboxStatus.claimed,
                    "claim_token": f"claim_{uuid4().hex}",
                    "claim_owner": owner,
                    "claimed_until": now + timedelta(seconds=lease_seconds),
                    "attempts": message.attempts + 1,
                }
            )
            self._messages_by_key[key] = updated
            claimed.append(updated)
            if len(claimed) >= limit:
                break
        return claimed

    def mark_published(self, principal: Principal, message_id: str, *, claim_token: str) -> OutboxMessage:
        require_trusted_principal(principal)
        for key, message in list(self._messages_by_key.items()):
            if key[0] == principal.tenant_id and message.id == message_id:
                if message.published:
                    return message
                if not message.claimed or message.claim_token != claim_token:
                    raise AssistantError(ErrorCode.PERMISSION_DENIED, "invalid outbox claim token", tenant_id=principal.tenant_id)
                updated = message.model_copy(
                    update={
                        "dispatch_status": OutboxStatus.published,
                        "claim_token": None,
                        "claim_owner": None,
                        "claimed_until": None,
                        "published_at": datetime.now(UTC),
                    }
                )
                self._messages_by_key[key] = updated
                return updated
        raise AssistantError(ErrorCode.NOT_FOUND, "outbox message not found", tenant_id=principal.tenant_id)

    def release(self, principal: Principal, message_id: str, *, claim_token: str) -> OutboxMessage:
        require_trusted_principal(principal)
        for key, message in list(self._messages_by_key.items()):
            if key[0] == principal.tenant_id and message.id == message_id:
                if message.claim_token != claim_token:
                    raise AssistantError(ErrorCode.PERMISSION_DENIED, "invalid outbox claim token", tenant_id=principal.tenant_id)
                updated = message.model_copy(
                    update={
                        "dispatch_status": OutboxStatus.pending,
                        "claim_token": None,
                        "claim_owner": None,
                        "claimed_until": None,
                    }
                )
                self._messages_by_key[key] = updated
                return updated
        raise AssistantError(ErrorCode.NOT_FOUND, "outbox message not found", tenant_id=principal.tenant_id)


class InMemoryWorkflowStateStore:
    def __init__(self) -> None:
        self._states_by_key: dict[tuple[str, str], WorkflowState] = {}

    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        require_trusted_principal(principal)
        if state.tenant_id != principal.tenant_id:
            raise AssistantError(ErrorCode.PERMISSION_DENIED, "workflow tenant mismatch", tenant_id=principal.tenant_id)
        key = (principal.tenant_id, state.idempotency_key)
        existing = self._states_by_key.get(key)
        if existing is not None and existing.status in {WorkflowStatus.completed, WorkflowStatus.failed}:
            if state.status != existing.status or _fingerprint(state.model_dump(mode="json")) != _fingerprint(
                existing.model_dump(mode="json")
            ):
                raise AssistantError(ErrorCode.CONFLICT, "terminal workflow state is immutable", tenant_id=principal.tenant_id)
        self._states_by_key[key] = state
        return state

    def get_by_idempotency_key(self, principal: Principal, idempotency_key: str) -> WorkflowState | None:
        require_trusted_principal(principal)
        return self._states_by_key.get((principal.tenant_id, idempotency_key))

    def list_for_tenant(self, principal: Principal) -> list[WorkflowState]:
        require_trusted_principal(principal)
        return [state for (tenant_id, _), state in self._states_by_key.items() if tenant_id == principal.tenant_id]
