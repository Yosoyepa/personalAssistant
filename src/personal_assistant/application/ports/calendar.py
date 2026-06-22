"""Calendar application port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class CalendarEventRequest(BaseModel):
    title: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime | None = None
    timezone: str = "America/Bogota"
    idempotency_key: str = Field(min_length=1)


class CalendarEventResult(BaseModel):
    event_id: str
    title: str
    starts_at: datetime
    idempotency_key: str
    reused: bool = False


class CalendarPort(Protocol):
    def create_event(
        self,
        principal: Principal,
        request: CalendarEventRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> CalendarEventResult:
        """Create or reuse an idempotent calendar event."""

    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        """List tenant-scoped calendar events for the authenticated principal."""


class CalendarReadPort(Protocol):
    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        """List tenant-scoped calendar events for read-only surfaces."""
