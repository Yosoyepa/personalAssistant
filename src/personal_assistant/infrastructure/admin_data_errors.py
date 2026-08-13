"""Normalized cross-source error fetcher for the admin dashboard."""

from __future__ import annotations

from collections import Counter
from typing import Any

from personal_assistant.application.dto.events import OutboxStatus
from personal_assistant.application.dto.tracing import TraceEventType
from personal_assistant.application.dto.workflows import WorkflowStatus
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_auth import DEFAULT_LIMIT, clamp_limit
from personal_assistant.infrastructure.admin_error_items import (
    _outbox_error_item,
    _trace_error_item,
    _workflow_error_item,
)
from personal_assistant.infrastructure.admin_shared import _tenant_outbox_messages
from personal_assistant.infrastructure.admin_trace_categories import (
    _trace_error_category,
)
from personal_assistant.infrastructure.admin_trace_filters import (
    _error_item_matches_filters,
    _event_type_value,
    _filter_trace_errors,
    _normalized_filter,
    _normalized_trace_run_id_filter,
    _trace_error_events,
    _trace_error_runs,
)
from personal_assistant.infrastructure.bootstrap import AppContainer


def fetch_errors(
    container: AppContainer,
    principal: Principal,
    *,
    category: str | None = None,
    run_id: str | None = None,
    event_type: str | TraceEventType | None = None,
    source: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Normalize trace, workflow, and outbox failures with active filters."""
    safe_limit = clamp_limit(limit)
    trace_events = _filter_trace_errors(
        _trace_error_events(container.traces.list_for_tenant(principal)),
        category=category,
        run_id=run_id,
        event_type=event_type,
    )
    traces = [
        _trace_error_item(trace)
        for trace in trace_events
    ]
    failed_states = [
        _workflow_error_item(state)
        for state in container.states.list_for_tenant(principal)
        if state.status == WorkflowStatus.failed
    ]
    failed_outbox = [
        _outbox_error_item(message)
        for message in _tenant_outbox_messages(container, principal.tenant_id)
        if message.dispatch_status == OutboxStatus.failed
    ]
    items = [
        item
        for item in [*traces, *failed_states, *failed_outbox]
        if _error_item_matches_filters(item, category=category, run_id=run_id, event_type=event_type, source=source)
    ]
    items = sorted(
        items,
        key=lambda item: item["timestamp"] or "",
        reverse=True,
    )
    return {
        "total": len(items),
        "counts": dict(Counter(item["source"] for item in items)),
        "category_counts": dict(Counter(item["category"] for item in items)),
        "trace_category_counts": dict(Counter(_trace_error_category(event) for event in trace_events)),
        "event_type_counts": dict(Counter(event.event_type.value for event in trace_events)),
        "run_count": len({event.run_id for event in trace_events}),
        "runs": _trace_error_runs(trace_events, limit=safe_limit),
        "filters": {
            "category": _normalized_filter(category),
            "run_id": _normalized_trace_run_id_filter(run_id),
            "event_type": _event_type_value(event_type),
            "source": _normalized_filter(source),
        },
        "items": items[:safe_limit],
    }
