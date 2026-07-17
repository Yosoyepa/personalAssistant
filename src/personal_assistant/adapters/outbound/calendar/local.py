"""Local calendar adapter used until Google Calendar is wired in."""

from __future__ import annotations

import hashlib
import json
from threading import RLock
from uuid import uuid4

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


class LocalCalendarTool:
    """P3 external-write calendar tool with idempotent local storage."""

    permission_tier = PermissionTier.P3

    def __init__(self) -> None:
        self._events_by_key: dict[tuple[str, str], CalendarEventResult] = {}
        self._key_by_event_id: dict[tuple[str, str], str] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def create_event(
        self,
        principal: Principal,
        request: CalendarEventRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> CalendarEventResult:
        require_approval(
            principal=principal,
            tier=self.permission_tier,
            approval=approval,
            action="calendar.create_event",
            resource=request.idempotency_key,
        )
        key = (principal.tenant_id, request.idempotency_key)
        request_fingerprint = _fingerprint(request)
        with self._lock:
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
