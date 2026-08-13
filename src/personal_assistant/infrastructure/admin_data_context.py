"""Context-utilization and guardrail hit-rate fetchers for the admin dashboard."""

from __future__ import annotations

from collections import Counter
from typing import Any

from personal_assistant.application.dto.tracing import TraceEventType
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_shared import (
    _context_utilization_value,
    _empty_context_component,
    _empty_guardrail_metrics,
    _guardrail_event_metrics,
    percentile_nearest_rank,
)
from personal_assistant.infrastructure.bootstrap import AppContainer


def fetch_context(container: AppContainer, principal: Principal) -> dict[str, Any]:
    """Aggregate LLM context-utilization stats from persisted traces.

    Reads ``llm.called`` events through the same public trace port as the
    other components and summarizes the ``context_utilization`` values
    recorded in their ``output_summary``. The source is read fail-closed:
    a failing or empty trace adapter degrades to the empty component
    instead of raising out of ``snapshot()``. Only numeric aggregates and
    model names leave this method, never event payloads.
    """
    try:
        events = container.traces.list_for_tenant(principal)
    except Exception:
        return _empty_context_component()
    llm_events = [
        event
        for event in events
        if event.event_type == TraceEventType.llm_called
    ]
    samples = [
        utilization
        for event in llm_events
        if (utilization := _context_utilization_value(event)) is not None
    ]
    return {
        "samples": len(samples),
        "p50": round(percentile_nearest_rank(samples, 50), 4) if samples else None,
        "p95": round(percentile_nearest_rank(samples, 95), 4) if samples else None,
        "calls_by_model": dict(
            Counter(event.model or "unknown" for event in llm_events)
        ),
    }


def fetch_guardrail_metrics(container: AppContainer, principal: Principal) -> dict[str, Any]:
    """Aggregate guardrail hit-rate metrics from persisted traces.

    Reads ``guardrail.checked`` events through the same public,
    tenant-scoped trace port as the other components and reduces their
    sanitized ``validation`` payloads to counts. Only events carrying
    the payload shape emitted by ``build_guardrail_validation`` are
    counted; malformed or foreign payloads are skipped so they never
    distort the metrics. The source is read fail-closed: a failing or
    empty trace adapter degrades to zero metrics instead of raising out
    of ``snapshot()``. Only numeric aggregates and category names leave
    this method, never event payloads or user content.
    """
    try:
        events = container.traces.list_for_tenant(principal)
    except Exception:
        return _empty_guardrail_metrics()
    scanned = 0
    action_counts: Counter[str] = Counter()
    category_counts: dict[str, Counter[str]] = {}
    for event in events:
        if event.event_type != TraceEventType.guardrail_checked:
            continue
        metrics = _guardrail_event_metrics(event.validation)
        if metrics is None:
            continue
        action, category_blocked = metrics
        scanned += 1
        action_counts[action] += 1
        for category, blocked in category_blocked.items():
            counter = category_counts.setdefault(category, Counter())
            counter["scanned"] += 1
            counter["blocked" if blocked else "flagged"] += 1
    blocked_total = action_counts["blocked"]
    return {
        "scanned": scanned,
        "allowed": action_counts["allowed"],
        "flagged": action_counts["flagged"],
        "blocked": blocked_total,
        "hit_rate": round(blocked_total / scanned, 4) if scanned else None,
        "categories": {
            category: {
                "scanned": counter["scanned"],
                "flagged": counter["flagged"],
                "blocked": counter["blocked"],
            }
            for category, counter in sorted(category_counts.items())
        },
    }
