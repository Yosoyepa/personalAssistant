"""Component-status assembly for the admin dashboard health summary."""

from __future__ import annotations

from typing import Any


def health_components(
    *,
    traces: dict[str, Any],
    outbox: dict[str, Any],
    scheduler: dict[str, Any],
    agenda: dict[str, Any],
    reminders: dict[str, Any],
    events: dict[str, Any],
    states: dict[str, Any],
    memory: dict[str, Any],
    errors: dict[str, Any],
    context: dict[str, Any],
    high_context_utilization: int,
) -> dict[str, Any]:
    """Build the per-component status map for the health section."""
    outbox_counts = outbox["counts"]
    state_counts = states["counts"]
    scheduler_counts = scheduler["counts"]
    return {
        "traces": {
            "status": "ok" if traces["error_count"] == 0 else "needs_attention",
            "total": traces["total"],
            "runs": traces["run_count"],
            "error_count": traces["error_count"],
        },
        "outbox": {"status": "ok", "total": outbox["total"], "counts": outbox_counts},
        "scheduler": {"status": "ok", "total": scheduler["total"], "counts": scheduler_counts},
        "agenda": {
            "status": "ok",
            "total": agenda["total"],
            "upcoming": agenda["upcoming_count"],
            "today": agenda["today_count"],
            "past": agenda["past_count"],
        },
        "reminders": {"status": "ok", "total": reminders["total"], "counts": reminders["counts"]},
        "errors": {
            "status": "ok" if errors["total"] == 0 else "needs_attention",
            "total": errors["total"],
            "runs": errors["run_count"],
            "counts": errors["counts"],
            "category_counts": errors["category_counts"],
        },
        "events": {"status": "ok", "total": events["total"]},
        "states": {"status": "ok", "total": states["total"], "counts": state_counts},
        "memory": {"status": "ok", "total": memory["total"], "confirmed": memory["confirmed_count"]},
        "context": {
            "status": "needs_attention" if high_context_utilization else "ok",
            "samples": context["samples"],
            "p95": context["p95"],
        },
    }
