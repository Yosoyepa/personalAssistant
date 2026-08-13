"""Approvals data fetcher for the admin dashboard."""

from __future__ import annotations

from typing import Any

from personal_assistant.application.dto.tracing import TraceEventType
from personal_assistant.application.dto.workflows import WorkflowStatus
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_auth import DEFAULT_LIMIT, clamp_limit
from personal_assistant.infrastructure.admin_items import (
    _approval_item,
    _trace_item,
    _workflow_state_item,
)
from personal_assistant.infrastructure.bootstrap import AppContainer


def fetch_approvals(
    container: AppContainer,
    principal: Principal,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Summarize pending approvals, waiting workflow states, and traces."""
    raw_states = sorted(
        container.states.list_for_tenant(principal),
        key=lambda state: state.updated_at,
        reverse=True,
    )
    raw_traces = sorted(
        container.traces.list_for_tenant(principal),
        key=lambda event: event.timestamp,
        reverse=True,
    )
    waiting_states = [_workflow_state_item(state) for state in raw_states if state.status == WorkflowStatus.waiting_approval]
    approval_traces = [_trace_item(trace) for trace in raw_traces if trace.event_type == TraceEventType.approval_requested]
    approval_store = getattr(container, "approvals", None)
    items: list[dict[str, Any]] = []
    if approval_store is not None:
        items = [
            _approval_item(approval)
            for approval in sorted(
                approval_store.list_for_tenant(principal),
                key=lambda approval: approval.created_at,
                reverse=True,
            )[: clamp_limit(limit)]
        ]
    return {
        "pending_count": len(waiting_states),
        "items": items,
        "workflow_states": waiting_states[: clamp_limit(limit)],
        "trace_events": approval_traces[: clamp_limit(limit)],
    }
