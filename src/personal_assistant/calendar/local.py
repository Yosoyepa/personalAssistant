"""Local calendar adapter used until Google Calendar is wired in."""

from __future__ import annotations

from uuid import uuid4

from personal_assistant.application.ports.calendar import CalendarEventRequest, CalendarEventResult
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier, require_approval
from personal_assistant.domain.common.identity import Principal


class LocalCalendarTool:
    """P3 external-write calendar tool with idempotent local storage."""

    permission_tier = PermissionTier.P3

    def __init__(self) -> None:
        self._events_by_key: dict[tuple[str, str], CalendarEventResult] = {}

    def create_event(
        self,
        principal: Principal,
        request: CalendarEventRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> CalendarEventResult:
        key = (principal.tenant_id, request.idempotency_key)
        existing = self._events_by_key.get(key)
        if existing is not None:
            return existing.model_copy(update={"reused": True})

        require_approval(
            principal=principal,
            tier=self.permission_tier,
            approval=approval,
            action="calendar.create_event",
            resource=request.idempotency_key,
        )
        result = CalendarEventResult(
            event_id=f"cal_{uuid4().hex}",
            title=request.title,
            starts_at=request.starts_at,
            idempotency_key=request.idempotency_key,
        )
        self._events_by_key[key] = result
        return result

    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        return [event for (tenant_id, _), event in self._events_by_key.items() if tenant_id == principal.tenant_id]
