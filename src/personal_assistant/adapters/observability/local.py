"""Local in-memory trace recorder adapter."""

from __future__ import annotations

from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.domain.common.identity import Principal, require_trusted_principal


class TraceRecorder:
    """In-memory trace recorder for local development."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self._events.append(event)

    def list_for_tenant(self, principal: Principal | str) -> list[TraceEvent]:
        tenant_id = _tenant_id_from_principal(principal)
        return [event for event in self._events if event.tenant_id == tenant_id]

    def list_for_run(self, principal: Principal | str, run_id: str) -> list[TraceEvent]:
        tenant_id = _tenant_id_from_principal(principal)
        return [event for event in self._events if event.tenant_id == tenant_id and event.run_id == run_id]


def _tenant_id_from_principal(principal: Principal | str) -> str:
    if isinstance(principal, str):
        return principal
    require_trusted_principal(principal)
    return principal.tenant_id
