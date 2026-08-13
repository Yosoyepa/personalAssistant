"""Shared HTML rendering helpers for the admin dashboard sections."""

from __future__ import annotations

import json
from html import escape
from typing import Any


def _render_summary_cards(cards: list[dict[str, Any]]) -> str:
    rendered = []
    for card in cards:
        tone = _safe_class_suffix(str(card.get("tone", "neutral")))
        rendered.append(
            "\n".join(
                [
                    f'<article class="summary-card summary-card-{tone}">',
                    f'<span class="summary-label">{_format_cell(card.get("label", ""))}</span>',
                    f'<strong class="summary-value">{_format_cell(card.get("value", ""))}</strong>',
                    f'<span class="summary-detail">{_format_cell(card.get("detail", ""))}</span>',
                    "</article>",
                ]
            )
        )
    return '<div class="summary-grid">' + "\n".join(rendered) + "</div>"


def _render_table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    labels = {column: column.replace("_", " ").title() for column in columns}
    header = "".join(f'<th scope="col">{escape(labels[column])}</th>' for column in columns)
    if not rows:
        body = f'<tr><td colspan="{len(columns)}" class="empty">No rows</td></tr>'
    else:
        body = "\n".join(
            "<tr>"
            + "".join(
                f'<td data-label="{escape(labels[column])}">{_format_cell(row.get(column, ""))}</td>'
                for column in columns
            )
            + "</tr>"
            for row in rows
        )
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _render_table_section(section_id: str, title: str, columns: list[str], rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            f'<section id="{escape(section_id)}">',
            f"<h2>{escape(title)}</h2>",
            _render_table(columns, rows),
            "</section>",
        ]
    )


def _format_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str, sort_keys=True)
    elif isinstance(value, bool):
        value = "yes" if value else "no"
    elif value is None:
        value = ""
    return escape(str(value))


def _safe_class_suffix(value: str) -> str:
    normalized = "".join(character if character.isalnum() or character == "-" else "-" for character in value.lower())
    return normalized.strip("-") or "neutral"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"
