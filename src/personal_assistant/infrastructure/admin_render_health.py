"""Health section renderer for the admin dashboard."""

from __future__ import annotations

from html import escape
from typing import Any

from personal_assistant.infrastructure.admin_render_helpers import (
    _render_summary_cards,
    _render_table,
)


def _render_health(health: dict[str, Any]) -> str:
    attention_total = sum(int(count) for count in health["attention"].values())
    component_rows = [
        {
            "component": name,
            "status": data["status"],
            "total": data.get("total", ""),
            "details": data.get("counts", data),
        }
        for name, data in health["components"].items()
    ]
    attention_rows = [
        {"signal": signal, "count": count}
        for signal, count in health["attention"].items()
    ]
    cards = [
        {
            "label": "Status",
            "value": health["status"],
            "detail": f"{attention_total} attention signal(s)",
            "tone": "ok" if health["status"] == "ok" else "attention",
        },
        {
            "label": "Components",
            "value": len(health["components"]),
            "detail": "runtime surfaces included",
        },
        {
            "label": "Pending approvals",
            "value": health["attention"].get("pending_approvals", 0),
            "detail": "workflow states waiting",
            "tone": "attention" if health["attention"].get("pending_approvals", 0) else "neutral",
        },
        {
            "label": "Errors",
            "value": health["attention"].get("errors", 0),
            "detail": "trace, workflow, and outbox failures",
            "tone": "danger" if health["attention"].get("errors", 0) else "neutral",
        },
    ]
    return "\n".join(
        [
            '<section id="health">',
            '<div class="section-heading">',
            "<h2>Health</h2>",
            f'<span class="status status-{escape(health["status"])}">{escape(health["status"])}</span>',
            "</div>",
            _render_summary_cards(cards),
            _render_table(["component", "status", "total", "details"], component_rows),
            '<h3>Attention</h3>',
            _render_table(["signal", "count"], attention_rows),
            "</section>",
        ]
    )
