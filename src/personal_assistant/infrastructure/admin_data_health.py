"""Health summary fetcher for the admin dashboard."""

from __future__ import annotations

from typing import Any

from personal_assistant.application.dto.events import OutboxStatus
from personal_assistant.application.dto.workflows import WorkflowStatus
from personal_assistant.infrastructure.admin_data_health_components import (
    health_components,
)
from personal_assistant.infrastructure.admin_shared import (
    CONTEXT_UTILIZATION_ATTENTION_THRESHOLD,
)


def fetch_health(
    *,
    traces: dict[str, Any],
    outbox: dict[str, Any],
    scheduler: dict[str, Any],
    agenda: dict[str, Any],
    reminders: dict[str, Any],
    events: dict[str, Any],
    states: dict[str, Any],
    memory: dict[str, Any],
    approvals: dict[str, Any],
    errors: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate the attention signals and component statuses for health."""
    outbox_counts = outbox["counts"]
    state_counts = states["counts"]
    scheduler_counts = scheduler["counts"]
    context_p95 = context["p95"]
    high_context_utilization = int(
        context_p95 is not None
        and context_p95 > CONTEXT_UTILIZATION_ATTENTION_THRESHOLD
    )
    attention = {
        "pending_approvals": approvals["pending_count"],
        "due_reminders": scheduler_counts["due"],
        "errors": errors["total"],
        "pending_outbox": outbox_counts.get(OutboxStatus.pending.value, 0),
        "claimed_outbox": outbox_counts.get(OutboxStatus.claimed.value, 0),
        "failed_outbox": outbox_counts.get(OutboxStatus.failed.value, 0),
        "failed_workflows": state_counts.get(WorkflowStatus.failed.value, 0),
        "high_context_utilization": high_context_utilization,
    }
    status = "needs_attention" if any(attention.values()) else "ok"
    return {
        "status": status,
        "attention": attention,
        "components": health_components(
            traces=traces,
            outbox=outbox,
            scheduler=scheduler,
            agenda=agenda,
            reminders=reminders,
            events=events,
            states=states,
            memory=memory,
            errors=errors,
            context=context,
            high_context_utilization=high_context_utilization,
        ),
    }
