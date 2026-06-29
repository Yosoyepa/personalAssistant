"""Observability application ports."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.domain.common.identity import Principal


class TraceRecorderPort(Protocol):
    def write(self, event: TraceEvent) -> None:
        """Persist one trace event."""

    def list_for_tenant(self, principal: Principal) -> list[TraceEvent]:
        """List trace events visible to the authenticated tenant."""

    def list_for_run(self, principal: Principal, run_id: str) -> list[TraceEvent]:
        """List trace events for one run visible to the authenticated tenant."""
