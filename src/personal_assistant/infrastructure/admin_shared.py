"""Shared tenant accessors and metric helpers for the admin dashboard."""

from __future__ import annotations

import math
from typing import Any

from personal_assistant.application.dto.events import OutboxMessage
from personal_assistant.application.dto.tracing import (
    GUARDRAIL_ACTIONS,
    TraceEvent,
)
from personal_assistant.application.ports.calendar import CalendarEventResult
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.guardrails import GuardrailSeverity
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_auth import local_admin_principal
from personal_assistant.infrastructure.bootstrap import AppContainer

CONTEXT_UTILIZATION_ATTENTION_THRESHOLD = 0.4


def _tenant_outbox_messages(container: AppContainer, tenant_id: str) -> list[OutboxMessage]:
    principal = local_admin_principal(tenant_id=tenant_id)
    return container.outbox.list_for_tenant(principal)


def _tenant_scheduler_jobs(container: AppContainer, tenant_id: str) -> list[ScheduledReminder]:
    principal = local_admin_principal(tenant_id=tenant_id)
    return container.scheduler.list_for_tenant(principal)


def _tenant_calendar_events(container: AppContainer, principal: Principal) -> list[CalendarEventResult]:
    calendar = getattr(container, "calendar", None)
    if calendar is None:
        return []
    list_events = getattr(calendar, "list_events", None)
    if not callable(list_events):
        return []
    return list_events(principal)


def percentile_nearest_rank(values: list[float], percentile: float) -> float:
    """Return the nearest-rank percentile of a non-empty sample list.

    Deterministic rule: sort ascending and pick the observed sample at rank
    ``ceil(percentile / 100 * n)`` (1-based). No interpolation: the result is
    always an observed value, so p95 only exceeds the attention threshold when
    an actual call did. ``percentile`` must be in (0, 100].
    """
    if not values:
        raise ValueError("percentile_nearest_rank requires at least one sample")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


def _context_utilization_value(event: TraceEvent) -> float | None:
    value = event.output_summary.get("context_utilization")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _guardrail_event_metrics(
    validation: dict[str, Any],
) -> tuple[str, dict[str, bool]] | None:
    """Reduce one sanitized guardrail payload to action and category hits.

    Returns ``None`` for payloads that do not match the emitted shape so
    malformed or foreign ``guardrail.checked`` events never distort the
    metrics. The boolean per category marks whether that category
    contributed a blocking (high-severity) finding; a category seen only
    through non-blocking findings counts as flagged.
    """
    action = validation.get("status")
    if not isinstance(action, str) or action not in GUARDRAIL_ACTIONS:
        return None
    findings = validation.get("findings")
    if not isinstance(findings, list):
        return None
    category_blocked: dict[str, bool] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            return None
        category = finding.get("category")
        if not isinstance(category, str) or not category:
            return None
        blocking = finding.get("severity") == GuardrailSeverity.HIGH.value
        category_blocked[category] = category_blocked.get(category, False) or blocking
    return action, category_blocked


def _empty_context_component() -> dict[str, Any]:
    return {"samples": 0, "p50": None, "p95": None, "calls_by_model": {}}


def _empty_guardrail_metrics() -> dict[str, Any]:
    return {
        "scanned": 0,
        "allowed": 0,
        "flagged": 0,
        "blocked": 0,
        "hit_rate": None,
        "categories": {},
    }
