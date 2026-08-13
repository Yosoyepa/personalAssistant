"""Error-events table and context section renderers for the admin dashboard."""

from __future__ import annotations

from html import escape
from typing import Any

from personal_assistant.infrastructure.admin_render_helpers import (
    _format_cell,
    _format_percent,
    _render_summary_cards,
    _render_table,
)
from personal_assistant.infrastructure.admin_shared import (
    CONTEXT_UTILIZATION_ATTENTION_THRESHOLD,
)


def _render_error_table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    labels = {column: column.replace("_", " ").title() for column in columns}
    header = "".join(f'<th scope="col">{escape(labels[column])}</th>' for column in columns)
    if not rows:
        body = f'<tr><td colspan="{len(columns)}" class="empty">No rows</td></tr>'
    else:
        body = "\n".join(
            '<tr data-trace-error-row '
            f'data-category="{escape(str(row.get("category", "")))}" '
            f'data-run-id="{escape(str(row.get("run_id", "")))}" '
            f'data-event-type="{escape(str(row.get("event_type", "")))}">'
            + "".join(
                f'<td data-label="{escape(labels[column])}">{_format_cell(row.get(column, ""))}</td>'
                for column in columns
            )
            + "</tr>"
            for row in rows
        )
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _render_context(context: dict[str, Any]) -> str:
    p95 = context["p95"]
    over_threshold = p95 is not None and p95 > CONTEXT_UTILIZATION_ATTENTION_THRESHOLD
    cards = [
        {
            "label": "Samples",
            "value": context["samples"],
            "detail": "llm.called events with usage data",
        },
        {
            "label": "P50 utilization",
            "value": _format_percent(context["p50"]),
            "detail": "nearest-rank median of input tokens over context window",
        },
        {
            "label": "P95 utilization",
            "value": _format_percent(p95),
            "detail": f"attention above {CONTEXT_UTILIZATION_ATTENTION_THRESHOLD:.0%}",
            "tone": "attention" if over_threshold else "neutral",
        },
        {
            "label": "Models",
            "value": len(context["calls_by_model"]),
            "detail": "models with llm.called events",
        },
    ]
    model_rows = [
        {"model": model, "calls": calls}
        for model, calls in sorted(context["calls_by_model"].items())
    ]
    return "\n".join(
        [
            '<section id="context">',
            '<div class="section-heading">',
            "<h2>Context</h2>",
            '<p class="section-note">Per-call LLM context utilization from persisted traces.</p>',
            "</div>",
            _render_summary_cards(cards),
            _render_table(["model", "calls"], model_rows),
            "</section>",
        ]
    )
