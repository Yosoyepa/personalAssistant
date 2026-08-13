"""Store-backed data fetchers for the admin dashboard (traces, outbox, state)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from personal_assistant.application.dto.events import OutboxStatus
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_auth import DEFAULT_LIMIT, clamp_limit
from personal_assistant.infrastructure.admin_items import (
    _event_item,
    _memory_item,
    _outbox_item,
    _trace_item,
    _workflow_state_item,
)
from personal_assistant.infrastructure.admin_shared import _tenant_outbox_messages
from personal_assistant.infrastructure.admin_trace_categories import (
    _trace_error_category,
)
from personal_assistant.infrastructure.admin_trace_filters import (
    _trace_error_events,
    _trace_error_runs,
)
from personal_assistant.infrastructure.bootstrap import AppContainer


def fetch_traces(
    container: AppContainer, principal: Principal, *, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """Summarize tenant trace events with error grouping for the dashboard."""
    safe_limit = clamp_limit(limit)
    events = sorted(
        container.traces.list_for_tenant(principal),
        key=lambda event: event.timestamp,
        reverse=True,
    )
    error_events = _trace_error_events(events)
    items = [_trace_item(event) for event in events[:safe_limit]]
    return {
        "total": len(events),
        "run_count": len({event.run_id for event in events}),
        "counts": dict(Counter(event.event_type.value for event in events)),
        "error_count": len(error_events),
        "error_category_counts": dict(Counter(_trace_error_category(event) for event in error_events)),
        "error_runs": _trace_error_runs(error_events, limit=safe_limit),
        "items": items,
    }


def fetch_outbox(
    container: AppContainer, principal: Principal, *, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """Summarize tenant outbox messages with dispatch-status counts."""
    messages = sorted(
        _tenant_outbox_messages(container, principal.tenant_id),
        key=lambda message: message.created_at,
        reverse=True,
    )
    return {
        "total": len(messages),
        "counts": dict(Counter(message.dispatch_status.value for message in messages)),
        "items": [_outbox_item(message) for message in messages[: clamp_limit(limit)]],
    }


def fetch_delivery_counts(container: AppContainer, principal: Principal) -> dict[str, int]:
    """Return only the closed delivery-state metric set, never row metadata."""

    observed = Counter(
        message.dispatch_status.value
        for message in _tenant_outbox_messages(
            container,
            principal.tenant_id,
        )
    )
    return {
        status.value: int(observed.get(status.value, 0))
        for status in OutboxStatus
    }


def fetch_events(
    container: AppContainer, principal: Principal, *, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """List tenant event-store rows with per-type counts."""
    events = sorted(
        container.event_store.list_for_tenant(principal),
        key=lambda event: event.time,
        reverse=True,
    )
    return {
        "total": len(events),
        "counts": dict(Counter(event.type for event in events)),
        "items": [_event_item(event) for event in events[: clamp_limit(limit)]],
    }


def fetch_states(
    container: AppContainer, principal: Principal, *, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """List durable-lite workflow states with per-status counts."""
    states = sorted(
        container.states.list_for_tenant(principal),
        key=lambda state: state.updated_at,
        reverse=True,
    )
    return {
        "total": len(states),
        "counts": dict(Counter(state.status.value for state in states)),
        "items": [_workflow_state_item(state) for state in states[: clamp_limit(limit)]],
    }


def fetch_memory(
    container: AppContainer, principal: Principal, *, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """List tenant memory records with confirmation and kind counts."""
    records = sorted(
        container.memory.list_for_tenant(principal),
        key=lambda record: record.created_at,
        reverse=True,
    )
    return {
        "total": len(records),
        "confirmed_count": len([record for record in records if record.confirmed]),
        "counts": dict(Counter(record.kind.value for record in records)),
        "items": [_memory_item(record) for record in records[: clamp_limit(limit)]],
    }
