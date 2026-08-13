"""Filtering and grouping of trace error events for the admin dashboard."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.domain.common.privacy import safe_trace_run_id
from personal_assistant.infrastructure.admin_auth import clamp_limit
from personal_assistant.infrastructure.admin_text import _preview, _string_value
from personal_assistant.infrastructure.admin_time import _iso
from personal_assistant.infrastructure.admin_trace_categories import (
    _trace_error_category,
)


def _trace_error_events(events: list[TraceEvent]) -> list[TraceEvent]:
    return [event for event in events if event.error or event.event_type == TraceEventType.agent_failed]


def _filter_trace_errors(
    events: list[TraceEvent],
    *,
    category: str | None,
    run_id: str | None,
    event_type: str | TraceEventType | None,
) -> list[TraceEvent]:
    normalized_category = _normalized_filter(category)
    normalized_run_id = _normalized_trace_run_id_filter(run_id)
    normalized_event_type = _event_type_value(event_type)
    return [
        event
        for event in events
        if (normalized_category is None or _trace_error_category(event) == normalized_category)
        and (normalized_run_id is None or event.run_id == normalized_run_id)
        and (normalized_event_type is None or event.event_type.value == normalized_event_type)
    ]


def _trace_error_runs(events: list[TraceEvent], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[TraceEvent]] = {}
    for event in events:
        grouped.setdefault(event.run_id, []).append(event)

    rows: list[dict[str, Any]] = []
    for run_id, run_events in grouped.items():
        sorted_events = sorted(run_events, key=lambda event: event.timestamp, reverse=True)
        latest = sorted_events[0]
        rows.append(
            {
                "latest_at": _iso(latest.timestamp),
                "first_at": _iso(sorted_events[-1].timestamp),
                "run_id": run_id,
                "count": len(sorted_events),
                "categories": dict(Counter(_trace_error_category(event) for event in sorted_events)),
                "event_types": dict(Counter(event.event_type.value for event in sorted_events)),
                "last_message": _trace_error_message(latest),
                "trace_ids": [event.trace_id for event in sorted_events],
            }
        )
    rows.sort(key=lambda row: str(row["latest_at"] or ""), reverse=True)
    return rows[: clamp_limit(limit)]


def _error_item_matches_filters(
    item: dict[str, Any],
    *,
    category: str | None,
    run_id: str | None,
    event_type: str | TraceEventType | None,
    source: str | None,
) -> bool:
    normalized_category = _normalized_filter(category)
    normalized_run_id = _normalized_trace_run_id_filter(run_id)
    normalized_event_type = _event_type_value(event_type)
    normalized_source = _normalized_filter(source)
    return (
        (normalized_category is None or item.get("category") == normalized_category)
        and (normalized_run_id is None or item.get("run_id") == normalized_run_id)
        and (normalized_event_type is None or item.get("event_type") == normalized_event_type)
        and (normalized_source is None or item.get("source") == normalized_source)
    )


def _normalized_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _normalized_trace_run_id_filter(value: str | None) -> str | None:
    """Return the privacy-safe, case-sensitive run-id query value.

    Run IDs are opaque identifiers, so matching is exact after surrounding
    whitespace is removed. Telegram-derived values are converted to the same
    stable digest used by trace persistence; an already-digested value remains
    searchable without exposing the original identifier in admin responses.
    """

    if value is None:
        return None
    normalized = safe_trace_run_id(value)
    return normalized or None


def _trace_error_operation(event: TraceEvent) -> str:
    for value in (
        event.tool_call.get("name"),
        event.input_summary.get("schema"),
        event.input_summary.get("prompt_id"),
    ):
        normalized = _string_value(value)
        if normalized:
            return normalized
    return event.event_type.value


def _trace_error_type(event: TraceEvent) -> str:
    for key in ("type", "code", "error_type"):
        value = _string_value(event.error.get(key))
        if value:
            return value
    return event.event_type.value


def _trace_error_message(event: TraceEvent) -> str:
    for key in ("message", "detail", "reason", "code"):
        value = _string_value(event.error.get(key))
        if value:
            return _preview(value, length=240)
    if event.error:
        return _preview(json.dumps(event.error, default=str, sort_keys=True), length=240)
    return ""


def _event_type_value(event_type: str | TraceEventType | None) -> str | None:
    if event_type is None:
        return None
    if isinstance(event_type, TraceEventType):
        return event_type.value
    return _normalized_filter(event_type)
