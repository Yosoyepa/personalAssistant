"""Local calendar adapter used until Google Calendar is wired in."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from personal_assistant.adapters._in_memory_transaction import (
    ReentrantLock,
    new_reentrant_lock,
)
from personal_assistant.application.ports.calendar import (
    CalendarEventRequest,
    CalendarEventResult,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionTier,
    require_approval,
)
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)


def _fingerprint(request: CalendarEventRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json", exclude={"event_id"}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _CalendarSnapshot:
    events_by_key: dict[tuple[str, str], CalendarEventResult]
    key_by_event_id: dict[tuple[str, str], str]
    fingerprints: dict[tuple[str, str], str]


class LocalCalendarTool:
    """P3 external-write calendar tool with idempotent local storage."""

    permission_tier = PermissionTier.P3

    def __init__(self) -> None:
        self._events_by_key: dict[tuple[str, str], CalendarEventResult] = {}
        self._key_by_event_id: dict[tuple[str, str], str] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._lock = new_reentrant_lock()

    @property
    def _reminder_transaction_lock(self) -> ReentrantLock:
        return self._lock

    def _snapshot_reminder_transaction(self) -> object:
        with self._lock:
            return _CalendarSnapshot(
                events_by_key=deepcopy(self._events_by_key),
                key_by_event_id=deepcopy(self._key_by_event_id),
                fingerprints=deepcopy(self._fingerprints),
            )

    def _restore_reminder_transaction(self, snapshot: object) -> None:
        if not isinstance(snapshot, _CalendarSnapshot):
            raise TypeError("invalid calendar transaction snapshot")
        with self._lock:
            self._events_by_key = deepcopy(snapshot.events_by_key)
            self._key_by_event_id = deepcopy(snapshot.key_by_event_id)
            self._fingerprints = deepcopy(snapshot.fingerprints)

    def create_event(
        self,
        principal: Principal,
        request: CalendarEventRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> CalendarEventResult:
        with self._lock:
            require_approval(
                principal=principal,
                tier=self.permission_tier,
                approval=approval,
                action="calendar.create_event",
                resource=request.idempotency_key,
            )
            key = (principal.tenant_id, request.idempotency_key)
            request_fingerprint = _fingerprint(request)
            existing = self._events_by_key.get(key)
            if existing is not None:
                if self._fingerprints[key] != request_fingerprint:
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "calendar idempotency conflict",
                        tenant_id=principal.tenant_id,
                    )
                return existing.model_copy(update={"reused": True})
            event_id = request.event_id or f"cal_{uuid4().hex}"
            event_key = (principal.tenant_id, event_id)
            existing_key = self._key_by_event_id.get(event_key)
            if existing_key is not None and existing_key != request.idempotency_key:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "calendar event id conflict",
                    tenant_id=principal.tenant_id,
                )
            result = CalendarEventResult(
                event_id=event_id,
                title=request.title,
                starts_at=request.starts_at,
                timezone=request.timezone,
                idempotency_key=request.idempotency_key,
                source_event_id=request.source_event_id,
                payload_fingerprint=request.payload_fingerprint,
            )
            self._events_by_key[key] = result
            self._key_by_event_id[event_key] = request.idempotency_key
            self._fingerprints[key] = request_fingerprint
            return result

    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        require_trusted_principal(principal)
        with self._lock:
            return [
                event.model_copy()
                for (tenant_id, _), event in self._events_by_key.items()
                if tenant_id == principal.tenant_id
            ]
