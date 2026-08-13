"""Errors section renderer and filter controls for the admin dashboard."""

from __future__ import annotations

from html import escape
from typing import Any

from personal_assistant.infrastructure.admin_assets import _ERROR_FILTER_SCRIPT
from personal_assistant.infrastructure.admin_render_helpers import (
    _render_summary_cards,
    _render_table,
)
from personal_assistant.infrastructure.admin_render_tables import _render_error_table
from personal_assistant.infrastructure.admin_text import _error_category_label


def _render_errors(errors: dict[str, Any]) -> str:
    counts = errors["counts"]
    category_counts = errors["category_counts"]
    cards = [
        {
            "label": "Open errors",
            "value": errors["total"],
            "detail": "from traces, workflows, and outbox",
            "tone": "danger" if errors["total"] else "ok",
        },
        {
            "label": "Trace runs",
            "value": errors["run_count"],
            "detail": "run ids with trace errors",
        },
        {
            "label": "Trace",
            "value": counts.get("trace", 0),
            "detail": "agent failures or trace error payloads",
        },
        {
            "label": "LLM",
            "value": category_counts.get("llm", 0),
            "detail": "model-call failures",
        },
        {
            "label": "Audio",
            "value": category_counts.get("audio", 0),
            "detail": "transcription or speech failures",
        },
    ]
    count_rows = [
        {"category": _error_category_label(category), "count": count}
        for category, count in sorted(category_counts.items())
    ]
    return "\n".join(
        [
            '<section id="errors">',
            '<div class="section-heading">',
            "<h2>Errors</h2>",
            '<p class="section-note">Failure rows are normalized across runtime sources.</p>',
            "</div>",
            _render_summary_cards(cards),
            _render_error_filters(errors),
            _render_table(["category", "count"], count_rows),
            "<h3>Runs</h3>",
            _render_table(["latest_at", "run_id", "count", "categories", "event_types", "last_message"], errors["runs"]),
            "<h3>Events</h3>",
            _render_error_table(
                ["timestamp", "category", "source", "type", "message", "run_id", "workflow_id", "event_type", "operation"],
                errors["items"],
            ),
            "</section>",
            _ERROR_FILTER_SCRIPT,
        ]
    )


def _render_error_filters(errors: dict[str, Any]) -> str:
    categories = ["all", *sorted(errors["category_counts"])]
    category_options = "\n".join(
        f'<option value="{escape(category)}">{escape(_error_category_label(category))}</option>'
        for category in categories
    )
    return (
        '<div class="filters" data-error-filters>\n'
        '<label>Category <select data-error-filter="category">\n'
        f"{category_options}\n"
        "</select></label>\n"
        '<label>Run ID <input type="search" data-error-filter="run_id" placeholder="run id">\n'
        "</div>"
    )
