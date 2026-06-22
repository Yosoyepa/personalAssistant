"""Observability application ports."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.application.dto.tracing import TraceEvent


class TraceRecorderPort(Protocol):
    def write(self, event: TraceEvent) -> None:
        """Persist one trace event."""
