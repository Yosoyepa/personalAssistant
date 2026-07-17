"""In-memory stores with tenant-scoped access and idempotency."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from personal_assistant.adapters._in_memory_transaction import (
    ReentrantLock,
    new_reentrant_lock,
)
from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
)
from personal_assistant.application.dto.delivery import (
    DeliveryError,
    MAX_CLAIM_LEASE_SECONDS,
    MAX_CLAIM_LIMIT,
    MAX_CLAIM_OWNER_LENGTH,
    canonical_utc,
    is_valid_claim_owner,
)
from personal_assistant.application.dto.workflows import (
    WorkflowState,
    WorkflowStateRegistration,
    WorkflowStatus,
)
from personal_assistant.application.dto.events import (
    CloudEvent,
    OutboxMessage,
    OutboxStatus,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionRequest,
    PermissionTier,
    require_permission,
)
from personal_assistant.domain.reminders.idempotency import ReminderIdempotencyConflict


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _approval_hash(approval: PendingApproval) -> str:
    return _fingerprint(
        {
            "approval_id": approval.approval_id,
            "tenant_id": approval.tenant_id,
            "principal_id": approval.principal_id,
            "action": approval.action,
            "resource": approval.resource,
            "tier": approval.tier,
            "workflow_kind": approval.workflow_kind,
            "idempotency_key": approval.idempotency_key,
            "message_id": approval.message_id,
            "source_event_id": approval.source_event_id,
            "conversation_id": approval.conversation_id,
            "channel": approval.channel,
            "recipient": approval.recipient,
            "request_text": approval.request_text,
            "timezone": approval.timezone,
            "payload_fingerprint": approval.payload_fingerprint,
        }
    )


@dataclass(frozen=True, slots=True)
class _EventStoreSnapshot:
    events_by_key: dict[tuple[str, str], CloudEvent]
    fingerprints: dict[tuple[str, str], str]


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events_by_key: dict[tuple[str, str], CloudEvent] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._lock = new_reentrant_lock()

    @property
    def _reminder_transaction_lock(self) -> ReentrantLock:
        return self._lock

    def _snapshot_reminder_transaction(self) -> object:
        with self._lock:
            return _EventStoreSnapshot(
                events_by_key=deepcopy(self._events_by_key),
                fingerprints=deepcopy(self._fingerprints),
            )

    def _restore_reminder_transaction(self, snapshot: object) -> None:
        if not isinstance(snapshot, _EventStoreSnapshot):
            raise TypeError("invalid event-store transaction snapshot")
        with self._lock:
            self._events_by_key = deepcopy(snapshot.events_by_key)
            self._fingerprints = deepcopy(snapshot.fingerprints)

    def append(self, principal: Principal, event: CloudEvent) -> CloudEvent:
        require_trusted_principal(principal)
        if event.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "event tenant mismatch",
                tenant_id=principal.tenant_id,
            )
        key = (principal.tenant_id, event.id)
        event_fingerprint = _fingerprint(event.model_dump(mode="json"))
        with self._lock:
            existing = self._events_by_key.get(key)
            if existing is not None:
                if self._fingerprints[key] != event_fingerprint:
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "event idempotency conflict",
                        tenant_id=principal.tenant_id,
                    )
                return existing
            self._events_by_key[key] = event
            self._fingerprints[key] = event_fingerprint
            return event

    def list_for_tenant(self, principal: Principal) -> list[CloudEvent]:
        require_trusted_principal(principal)
        with self._lock:
            return [
                event.model_copy(deep=True)
                for (tenant_id, _), event in self._events_by_key.items()
                if tenant_id == principal.tenant_id
            ]


@dataclass(frozen=True, slots=True)
class _OutboxSnapshot:
    messages_by_key: dict[tuple[str, str], OutboxMessage]
    key_by_message_id: dict[tuple[str, str], str]
    key_by_event_id: dict[tuple[str, str], str]
    fingerprints: dict[tuple[str, str], str]


class InMemoryOutbox:
    def __init__(self) -> None:
        self._messages_by_key: dict[tuple[str, str], OutboxMessage] = {}
        self._key_by_message_id: dict[tuple[str, str], str] = {}
        self._key_by_event_id: dict[tuple[str, str], str] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._lock = new_reentrant_lock()

    @property
    def _reminder_transaction_lock(self) -> ReentrantLock:
        return self._lock

    def _snapshot_reminder_transaction(self) -> object:
        with self._lock:
            return _OutboxSnapshot(
                messages_by_key=deepcopy(self._messages_by_key),
                key_by_message_id=deepcopy(self._key_by_message_id),
                key_by_event_id=deepcopy(self._key_by_event_id),
                fingerprints=deepcopy(self._fingerprints),
            )

    def _restore_reminder_transaction(self, snapshot: object) -> None:
        if not isinstance(snapshot, _OutboxSnapshot):
            raise TypeError("invalid outbox transaction snapshot")
        with self._lock:
            self._messages_by_key = deepcopy(snapshot.messages_by_key)
            self._key_by_message_id = deepcopy(snapshot.key_by_message_id)
            self._key_by_event_id = deepcopy(snapshot.key_by_event_id)
            self._fingerprints = deepcopy(snapshot.fingerprints)

    def add(
        self,
        principal: Principal,
        event: CloudEvent,
        *,
        idempotency_key: str,
        next_attempt_at: datetime | None = None,
        message_id: str | None = None,
    ) -> OutboxMessage:
        require_trusted_principal(principal)
        if event.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "outbox tenant mismatch",
                tenant_id=principal.tenant_id,
            )
        if next_attempt_at is not None:
            if next_attempt_at.tzinfo is None or next_attempt_at.utcoffset() is None:
                raise ValueError("next_attempt_at must be timezone-aware")
            next_attempt_at = next_attempt_at.astimezone(UTC)
        key = (principal.tenant_id, idempotency_key)
        write_fingerprint = _fingerprint(
            {
                "event": event.model_dump(mode="json"),
                "next_attempt_at": next_attempt_at,
                "message_id": message_id,
            }
        )
        with self._lock:
            existing = self._messages_by_key.get(key)
            if existing is not None:
                if self._fingerprints[key] != write_fingerprint:
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "outbox idempotency conflict",
                        tenant_id=principal.tenant_id,
                    )
                return existing.model_copy(deep=True)
            values: dict[str, object] = {
                "tenant_id": principal.tenant_id,
                "event": event,
                "idempotency_key": idempotency_key,
                "next_attempt_at": next_attempt_at,
            }
            if message_id is not None:
                values["id"] = message_id
            message = OutboxMessage.model_validate(values)

            message_key = (principal.tenant_id, message.id)
            existing_key = self._key_by_message_id.get(message_key)
            if existing_key is not None and existing_key != idempotency_key:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "outbox message id conflict",
                    tenant_id=principal.tenant_id,
                )

            event_key = (principal.tenant_id, event.id)
            existing_key = self._key_by_event_id.get(event_key)
            if existing_key is not None and existing_key != idempotency_key:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "outbox event id conflict",
                    tenant_id=principal.tenant_id,
                )
            self._messages_by_key[key] = message
            self._key_by_message_id[message_key] = idempotency_key
            self._key_by_event_id[event_key] = idempotency_key
            self._fingerprints[key] = write_fingerprint
            return message.model_copy(deep=True)

    def claim(
        self,
        principal: Principal,
        limit: int = 10,
        *,
        owner: str = "local-worker",
        lease_seconds: int = 60,
    ) -> list[OutboxMessage]:
        require_trusted_principal(principal)
        return self.claim_due(
            principal,
            datetime.now(UTC),
            limit=limit,
            owner=owner,
            lease_seconds=lease_seconds,
        )

    def claim_due(
        self,
        principal: Principal,
        now: datetime,
        *,
        limit: int = 10,
        owner: str = "local-worker",
        lease_seconds: int = 60,
    ) -> list[OutboxMessage]:
        require_trusted_principal(principal)
        now = canonical_utc(now, field="now")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if not 1 <= limit <= MAX_CLAIM_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_CLAIM_LIMIT}")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise TypeError("lease_seconds must be an integer")
        if not 1 <= lease_seconds <= MAX_CLAIM_LEASE_SECONDS:
            raise ValueError(
                f"lease_seconds must be between 1 and {MAX_CLAIM_LEASE_SECONDS}"
            )
        if not isinstance(owner, str):
            raise TypeError("owner must be a string")
        normalized_owner = owner.strip()
        if not normalized_owner:
            raise ValueError("owner must not be blank")
        if len(normalized_owner) > MAX_CLAIM_OWNER_LENGTH:
            raise ValueError(
                f"owner must be at most {MAX_CLAIM_OWNER_LENGTH} characters"
            )
        if not is_valid_claim_owner(normalized_owner):
            raise ValueError("owner contains unsupported characters")
        claimed: list[OutboxMessage] = []
        with self._lock:
            candidates = sorted(
                self._messages_by_key.items(),
                key=lambda item: (item[1].created_at, item[1].id),
            )
            for key, message in candidates:
                if key[0] != principal.tenant_id:
                    continue
                is_pending = message.dispatch_status == OutboxStatus.pending
                is_expired_claim = (
                    message.dispatch_status == OutboxStatus.claimed
                    and message.claimed_until is not None
                    and message.claimed_until <= now
                )
                if not (is_pending or is_expired_claim):
                    continue
                if (
                    message.next_attempt_at is not None
                    and message.next_attempt_at > now
                ):
                    continue
                updated = message.model_copy(
                    update={
                        "dispatch_status": OutboxStatus.claimed,
                        "claim_token": f"claim_{uuid4().hex}",
                        "claim_owner": normalized_owner,
                        "claimed_until": now + timedelta(seconds=lease_seconds),
                    }
                )
                self._messages_by_key[key] = updated
                claimed.append(updated.model_copy(deep=True))
                if len(claimed) >= limit:
                    break
        return claimed

    def mark_sending(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        started_at: datetime,
    ) -> OutboxMessage:
        return self._transition(
            principal,
            message_id,
            claim_token=claim_token,
            expected=OutboxStatus.claimed,
            target=OutboxStatus.sending,
            updates={
                "sending_at": canonical_utc(started_at, field="started_at"),
            },
            increment_attempts=True,
        )

    def mark_published(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        published_at: datetime | None = None,
    ) -> OutboxMessage:
        effective_time = published_at or datetime.now(UTC)
        return self._transition(
            principal,
            message_id,
            claim_token=claim_token,
            expected=OutboxStatus.sending,
            target=OutboxStatus.published,
            updates={
                "claim_token": None,
                "claim_owner": None,
                "claimed_until": None,
                "published_at": canonical_utc(effective_time, field="published_at"),
                "last_error": None,
            },
        )

    def mark_failed(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        error: DeliveryError,
    ) -> OutboxMessage:
        return self._terminal_error(
            principal,
            message_id,
            claim_token=claim_token,
            target=OutboxStatus.failed,
            error=error,
        )

    def mark_uncertain(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        error: DeliveryError,
    ) -> OutboxMessage:
        return self._terminal_error(
            principal,
            message_id,
            claim_token=claim_token,
            target=OutboxStatus.uncertain,
            error=error,
        )

    def release(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        next_attempt_at: datetime | None = None,
    ) -> OutboxMessage:
        normalized_next = (
            canonical_utc(next_attempt_at, field="next_attempt_at")
            if next_attempt_at is not None
            else None
        )
        return self._transition(
            principal,
            message_id,
            claim_token=claim_token,
            expected=OutboxStatus.claimed,
            target=OutboxStatus.pending,
            updates={
                "claim_token": None,
                "claim_owner": None,
                "claimed_until": None,
                "next_attempt_at": normalized_next,
            },
        )

    def reschedule(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        next_attempt_at: datetime,
        error: DeliveryError,
    ) -> OutboxMessage:
        return self._transition(
            principal,
            message_id,
            claim_token=claim_token,
            expected=OutboxStatus.sending,
            target=OutboxStatus.pending,
            updates={
                "claim_token": None,
                "claim_owner": None,
                "claimed_until": None,
                "next_attempt_at": canonical_utc(
                    next_attempt_at, field="next_attempt_at"
                ),
                "sending_at": None,
                "last_error": error,
            },
        )

    def _terminal_error(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        target: OutboxStatus,
        error: DeliveryError,
    ) -> OutboxMessage:
        return self._transition(
            principal,
            message_id,
            claim_token=claim_token,
            expected=OutboxStatus.sending,
            target=target,
            updates={
                "claim_token": None,
                "claim_owner": None,
                "claimed_until": None,
                "last_error": error,
            },
        )

    def _transition(
        self,
        principal: Principal,
        message_id: str,
        *,
        claim_token: str,
        expected: OutboxStatus,
        target: OutboxStatus,
        updates: dict[str, object],
        increment_attempts: bool = False,
    ) -> OutboxMessage:
        require_trusted_principal(principal)
        if not claim_token:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "invalid outbox claim token",
                tenant_id=principal.tenant_id,
            )
        with self._lock:
            for key, message in self._messages_by_key.items():
                if key[0] != principal.tenant_id or message.id != message_id:
                    continue
                if message.claim_token != claim_token:
                    raise AssistantError(
                        ErrorCode.PERMISSION_DENIED,
                        "invalid outbox claim token",
                        tenant_id=principal.tenant_id,
                    )
                if message.dispatch_status != expected:
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "illegal outbox delivery transition",
                        tenant_id=principal.tenant_id,
                    )
                transition_updates = {"dispatch_status": target, **updates}
                if increment_attempts:
                    transition_updates["attempts"] = message.attempts + 1
                updated = OutboxMessage.model_validate(
                    message.model_copy(update=transition_updates).model_dump()
                )
                self._messages_by_key[key] = updated
                return updated.model_copy(deep=True)
        raise AssistantError(
            ErrorCode.NOT_FOUND,
            "outbox message not found",
            tenant_id=principal.tenant_id,
        )

    def list_for_tenant(self, principal: Principal) -> list[OutboxMessage]:
        require_trusted_principal(principal)
        with self._lock:
            return [
                message.model_copy(deep=True)
                for (tenant_id, _), message in self._messages_by_key.items()
                if tenant_id == principal.tenant_id
            ]


@dataclass(frozen=True, slots=True)
class _WorkflowStateSnapshot:
    states_by_key: dict[tuple[str, str], WorkflowState]
    key_by_workflow_id: dict[tuple[str, str], str]


class InMemoryWorkflowStateStore:
    def __init__(self) -> None:
        self._states_by_key: dict[tuple[str, str], WorkflowState] = {}
        self._key_by_workflow_id: dict[tuple[str, str], str] = {}
        self._lock = new_reentrant_lock()

    @property
    def _reminder_transaction_lock(self) -> ReentrantLock:
        return self._lock

    def _snapshot_reminder_transaction(self) -> object:
        with self._lock:
            return _WorkflowStateSnapshot(
                states_by_key=deepcopy(self._states_by_key),
                key_by_workflow_id=deepcopy(self._key_by_workflow_id),
            )

    def _restore_reminder_transaction(self, snapshot: object) -> None:
        if not isinstance(snapshot, _WorkflowStateSnapshot):
            raise TypeError("invalid workflow-state transaction snapshot")
        with self._lock:
            self._states_by_key = deepcopy(snapshot.states_by_key)
            self._key_by_workflow_id = deepcopy(snapshot.key_by_workflow_id)

    def register_or_replay(
        self,
        principal: Principal,
        state: WorkflowState,
        *,
        resume_from_step: str | None = None,
    ) -> WorkflowStateRegistration:
        """Atomically elect one executor for a canonical event identity."""

        require_trusted_principal(principal)
        self._require_matching_tenant(principal, state)
        if state.payload_fingerprint is None:
            raise ValueError(
                "payload_fingerprint is required for workflow registration"
            )

        key = (principal.tenant_id, state.idempotency_key)
        workflow_id_key = (principal.tenant_id, state.workflow_id)
        with self._lock:
            existing = self._states_by_key.get(key)
            if existing is not None:
                self._raise_if_payload_conflicts(principal, existing, state)
                if existing.workflow_type != state.workflow_type:
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "workflow identity is immutable",
                        tenant_id=principal.tenant_id,
                    )
                if (
                    resume_from_step is not None
                    and existing.status == WorkflowStatus.waiting_approval
                    and existing.step == resume_from_step
                ):
                    resumed = existing.transition(status=WorkflowStatus.running)
                    self._states_by_key[key] = resumed
                    return WorkflowStateRegistration(
                        state=resumed.model_copy(deep=True),
                        replayed=False,
                        resumed=True,
                    )
                return WorkflowStateRegistration(
                    state=existing.model_copy(deep=True), replayed=True
                )

            existing_key = self._key_by_workflow_id.get(workflow_id_key)
            if existing_key is not None and existing_key != state.idempotency_key:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "workflow identity is immutable",
                    tenant_id=principal.tenant_id,
                )

            saved = state.model_copy(deep=True)
            self._states_by_key[key] = saved
            self._key_by_workflow_id[workflow_id_key] = state.idempotency_key
            return WorkflowStateRegistration(
                state=saved.model_copy(deep=True), replayed=False
            )

    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        require_trusted_principal(principal)
        self._require_matching_tenant(principal, state)
        key = (principal.tenant_id, state.idempotency_key)
        workflow_id_key = (principal.tenant_id, state.workflow_id)
        with self._lock:
            existing_key = self._key_by_workflow_id.get(workflow_id_key)
            if existing_key is not None and existing_key != state.idempotency_key:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "workflow identity is immutable",
                    tenant_id=principal.tenant_id,
                )
            existing = self._states_by_key.get(key)
            if existing is not None:
                if (
                    existing.workflow_id != state.workflow_id
                    or existing.workflow_type != state.workflow_type
                ):
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "workflow identity is immutable",
                        tenant_id=principal.tenant_id,
                    )
                self._raise_if_payload_conflicts(principal, existing, state)
                if existing.status in {WorkflowStatus.completed, WorkflowStatus.failed}:
                    if state.status != existing.status or _fingerprint(
                        state.model_dump(mode="json")
                    ) != _fingerprint(existing.model_dump(mode="json")):
                        raise AssistantError(
                            ErrorCode.CONFLICT,
                            "terminal workflow state is immutable",
                            tenant_id=principal.tenant_id,
                        )
            saved = state.model_copy(deep=True)
            self._states_by_key[key] = saved
            self._key_by_workflow_id[workflow_id_key] = state.idempotency_key
            return saved.model_copy(deep=True)

    def get_by_idempotency_key(
        self, principal: Principal, idempotency_key: str
    ) -> WorkflowState | None:
        require_trusted_principal(principal)
        with self._lock:
            state = self._states_by_key.get((principal.tenant_id, idempotency_key))
            return state.model_copy(deep=True) if state is not None else None

    def list_for_tenant(self, principal: Principal) -> list[WorkflowState]:
        require_trusted_principal(principal)
        with self._lock:
            return [
                state.model_copy(deep=True)
                for (tenant_id, _), state in self._states_by_key.items()
                if tenant_id == principal.tenant_id
            ]

    @staticmethod
    def _require_matching_tenant(principal: Principal, state: WorkflowState) -> None:
        if state.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "workflow tenant mismatch",
                tenant_id=principal.tenant_id,
            )

    @staticmethod
    def _raise_if_payload_conflicts(
        principal: Principal,
        existing: WorkflowState,
        candidate: WorkflowState,
    ) -> None:
        if existing.payload_fingerprint != candidate.payload_fingerprint:
            raise ReminderIdempotencyConflict(
                tenant_id=principal.tenant_id,
                idempotency_key=existing.idempotency_key,
            )


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._approvals_by_id: dict[tuple[str, str], PendingApproval] = {}
        self._approval_id_by_key: dict[tuple[str, str, str, str], str] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}

    def create(
        self, principal: Principal, approval: PendingApproval
    ) -> PendingApproval:
        require_trusted_principal(principal)
        if (
            approval.tenant_id != principal.tenant_id
            or approval.principal_id != principal.principal_id
        ):
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "approval principal mismatch",
                tenant_id=principal.tenant_id,
            )
        tier = PermissionTier(approval.tier)
        if tier.rank < PermissionTier.P3.rank:
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "approval requests are only valid for P3+ actions",
                tenant_id=principal.tenant_id,
            )
        idempotency_key = (
            principal.tenant_id,
            principal.principal_id,
            approval.workflow_kind,
            approval.idempotency_key,
        )
        approval_fingerprint = _approval_hash(approval)
        existing_id = self._approval_id_by_key.get(idempotency_key)
        if existing_id is not None:
            existing_key = (principal.tenant_id, existing_id)
            if self._fingerprints[existing_key] != approval_fingerprint:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "approval request idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return self._approvals_by_id[existing_key].model_copy(deep=True)
        key = (principal.tenant_id, approval.approval_id)
        self._approvals_by_id[key] = approval.model_copy(deep=True)
        self._approval_id_by_key[idempotency_key] = approval.approval_id
        self._fingerprints[key] = approval_fingerprint
        return approval.model_copy(deep=True)

    def get(self, principal: Principal, approval_id: str) -> PendingApproval | None:
        require_trusted_principal(principal)
        approval = self._approvals_by_id.get((principal.tenant_id, approval_id))
        if approval is None or approval.principal_id != principal.principal_id:
            return None
        return approval.model_copy(deep=True)

    def list_pending(self, principal: Principal) -> list[PendingApproval]:
        require_trusted_principal(principal)
        return [
            approval.model_copy(deep=True)
            for (tenant_id, _), approval in self._approvals_by_id.items()
            if tenant_id == principal.tenant_id
            and approval.principal_id == principal.principal_id
            and approval.status == PendingApprovalStatus.pending
        ]

    def list_for_tenant(self, principal: Principal) -> list[PendingApproval]:
        require_trusted_principal(principal)
        return [
            approval.model_copy(deep=True)
            for (tenant_id, _), approval in self._approvals_by_id.items()
            if tenant_id == principal.tenant_id
            and approval.principal_id == principal.principal_id
        ]

    def mark_approved(self, principal: Principal, approval_id: str) -> PendingApproval:
        approval = self.get(principal, approval_id)
        if approval is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if approval.status != PendingApprovalStatus.pending:
            return approval
        updated = approval.model_copy(
            update={
                "status": PendingApprovalStatus.approved,
                "updated_at": datetime.now(UTC),
            }
        )
        self._approvals_by_id[(principal.tenant_id, approval_id)] = updated
        return updated

    def approve(self, principal: Principal, approval_id: str) -> ApprovalGrant:
        approval = self.get(principal, approval_id)
        if approval is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if approval.status == PendingApprovalStatus.cancelled:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "approval was cancelled",
                tenant_id=principal.tenant_id,
            )
        tier = PermissionTier(approval.tier)
        require_permission(
            principal,
            PermissionRequest(
                action=approval.action, resource=approval.resource, required_tier=tier
            ),
        )
        if approval.status == PendingApprovalStatus.pending:
            approval = self.mark_approved(principal, approval_id)
        return ApprovalGrant.issue(
            principal=principal,
            action=approval.action,
            resource=approval.resource,
            tier=tier,
            approval_id=approval.approval_id,
            request_hash=_approval_hash(approval),
        )

    def cancel(self, principal: Principal, approval_id: str) -> PendingApproval:
        approval = self.get(principal, approval_id)
        if approval is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if approval.status == PendingApprovalStatus.approved:
            raise AssistantError(
                ErrorCode.CONFLICT,
                "approved approval cannot be cancelled",
                tenant_id=principal.tenant_id,
            )
        if approval.status == PendingApprovalStatus.cancelled:
            return approval
        updated = approval.model_copy(
            update={
                "status": PendingApprovalStatus.cancelled,
                "updated_at": datetime.now(UTC),
            }
        )
        self._approvals_by_id[(principal.tenant_id, approval_id)] = updated
        return updated

    def reject(self, principal: Principal, approval_id: str) -> PendingApproval:
        return self.cancel(principal, approval_id)
