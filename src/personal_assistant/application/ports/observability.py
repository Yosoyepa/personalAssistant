"""Observability application ports."""

from __future__ import annotations

from typing import Any, Protocol

from personal_assistant.application.dto.tracing import (
    TraceEvent,
    TraceEventType,
    require_trace_completeness,
)
from personal_assistant.domain.common.identity import Principal


class TraceRecorderPort(Protocol):
    def write(self, event: TraceEvent) -> None:
        """Persist one trace event."""

    def list_for_tenant(self, principal: Principal) -> list[TraceEvent]:
        """List trace events visible to the authenticated tenant."""

    def list_for_run(self, principal: Principal, run_id: str) -> list[TraceEvent]:
        """List trace events for one run visible to the authenticated tenant."""


def emit_guardrail_checked(
    recorder: TraceRecorderPort,
    *,
    agent_id: str,
    tenant_id: str,
    validation: dict[str, Any],
    run_id: str | None = None,
) -> TraceEvent:
    """Write one complete ``guardrail.checked`` trace event.

    ``validation`` must be the sanitized payload produced by
    :func:`personal_assistant.application.dto.tracing.build_guardrail_validation`;
    it carries only the scan action, category names, severities, and rule
    labels, never excerpts or user content. Completeness is enforced here as
    well as by conforming recorders, so an empty or redacted-away payload
    fails closed with ``IncompleteTraceEventError`` before anything is
    persisted. Returns the event as accepted (privacy-redacted at
    construction).
    """

    if run_id is None:
        event = TraceEvent(
            agent_id=agent_id,
            event_type=TraceEventType.guardrail_checked,
            tenant_id=tenant_id,
            validation=validation,
        )
    else:
        event = TraceEvent(
            run_id=run_id,
            agent_id=agent_id,
            event_type=TraceEventType.guardrail_checked,
            tenant_id=tenant_id,
            validation=validation,
        )
    require_trace_completeness(event)
    recorder.write(event)
    return event
