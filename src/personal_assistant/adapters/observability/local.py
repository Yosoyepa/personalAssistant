"""Local in-memory trace recorder adapter."""

from __future__ import annotations

from personal_assistant.application.dto.tracing import TraceEvent


class TraceRecorder:
    """In-memory trace recorder for local development."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self._events.append(event)

    def list_for_tenant(self, tenant_id: str) -> list[TraceEvent]:
        return [event for event in self._events if event.tenant_id == tenant_id]

    def list_for_run(self, tenant_id: str, run_id: str) -> list[TraceEvent]:
        return [event for event in self._events if event.tenant_id == tenant_id and event.run_id == run_id]
