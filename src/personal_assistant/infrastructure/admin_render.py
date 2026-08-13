"""Full-page HTML assembly for the local admin dashboard."""

from __future__ import annotations

from typing import Any

from personal_assistant.infrastructure.admin_assets import _CSS
from personal_assistant.infrastructure.admin_render_agenda import (
    _render_agenda,
    _render_reminders,
)
from personal_assistant.infrastructure.admin_render_approvals import _render_approvals
from personal_assistant.infrastructure.admin_render_errors import _render_errors
from personal_assistant.infrastructure.admin_render_header import _render_header
from personal_assistant.infrastructure.admin_render_health import _render_health
from personal_assistant.infrastructure.admin_render_helpers import _render_table_section
from personal_assistant.infrastructure.admin_render_outbox import _render_outbox
from personal_assistant.infrastructure.admin_render_tables import _render_context


def render_dashboard_html(snapshot: dict[str, Any]) -> str:
    """Render the complete dashboard page from one assembled snapshot."""
    meta = snapshot["meta"]
    health = snapshot["health"]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Personal Assistant Admin</title>",
            "<style>",
            _CSS,
            "</style>",
            "</head>",
            "<body>",
            *_render_header(meta),
            '<main class="container">',
            _render_health(health),
            _render_agenda(snapshot["agenda"]),
            _render_reminders(snapshot["reminders"]),
            _render_errors(snapshot["errors"]),
            _render_approvals(snapshot["approvals"]),
            _render_table_section(
                "traces",
                "Traces",
                ["timestamp", "run_id", "event_type", "agent_id", "tool_call", "validation", "error"],
                snapshot["traces"]["items"],
            ),
            _render_context(snapshot["context"]),
            _render_outbox(snapshot["outbox"]),
            _render_table_section(
                "scheduler",
                "Scheduler",
                ["notify_at", "reminder_id", "status", "due", "channel", "recipient", "body_preview", "idempotency_key"],
                snapshot["scheduler"]["items"],
            ),
            _render_table_section(
                "events",
                "Events",
                ["time", "id", "type", "source", "subject", "correlation_id", "data"],
                snapshot["events"]["items"],
            ),
            _render_table_section(
                "states",
                "States",
                ["updated_at", "workflow_id", "workflow_type", "status", "step", "idempotency_key", "data"],
                snapshot["states"]["items"],
            ),
            _render_table_section(
                "memory",
                "Memory",
                ["created_at", "id", "kind", "confirmed", "source", "text_preview"],
                snapshot["memory"]["items"],
            ),
            "</main>",
            "</body>",
            "</html>",
        ]
    )
