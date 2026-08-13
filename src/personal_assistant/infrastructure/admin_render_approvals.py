"""Approvals section renderer with operator actions for the admin dashboard."""

from __future__ import annotations

from html import escape
from typing import Any

from personal_assistant.infrastructure.admin_assets import (
    _APPROVAL_ACTIONS_SCRIPT,
)
from personal_assistant.infrastructure.admin_render_helpers import (
    _format_cell,
    _render_table,
)

_APPROVAL_COLUMNS = ["approval_id", "action", "resource", "status", "title", "created_at"]


def _render_approvals(approvals: dict[str, Any]) -> str:
    return "\n".join(
        [
            '<section id="approvals">',
            '<div class="section-heading">',
            "<h2>Approvals</h2>",
            '<p class="section-note">Pending approvals can be approved or rejected here; '
            "actions call the runtime API with the bearer token entered above.</p>",
            "</div>",
            '<p id="approval-action-status" class="section-note" role="status" aria-live="polite"></p>',
            _render_approvals_table(approvals["items"]),
            "<h3>Waiting workflow states</h3>",
            _render_table(
                ["workflow_id", "workflow_type", "status", "step", "idempotency_key", "updated_at", "data"],
                approvals["workflow_states"],
            ),
            "</section>",
            _APPROVAL_ACTIONS_SCRIPT,
        ]
    )


def _render_approvals_table(rows: list[dict[str, Any]]) -> str:
    labels = {column: column.replace("_", " ").title() for column in _APPROVAL_COLUMNS}
    labels["actions"] = "Actions"
    header = "".join(f'<th scope="col">{escape(labels[column])}</th>' for column in [*_APPROVAL_COLUMNS, "actions"])
    if not rows:
        body = f'<tr><td colspan="{len(_APPROVAL_COLUMNS) + 1}" class="empty">No rows</td></tr>'
    else:
        body = "\n".join(_render_approval_row(row, labels) for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _render_approval_row(row: dict[str, Any], labels: dict[str, str]) -> str:
    cells = []
    for column in _APPROVAL_COLUMNS:
        marker = " data-approval-status" if column == "status" else ""
        cells.append(f'<td data-label="{escape(labels[column])}"{marker}>{_format_cell(row.get(column, ""))}</td>')
    cells.append(f'<td data-label="Actions">{_approval_actions_cell(row)}</td>')
    return "<tr>" + "".join(cells) + "</tr>"


def _approval_actions_cell(row: dict[str, Any]) -> str:
    if row.get("status") != "pending":
        return ""
    approval_id = escape(str(row.get("approval_id", "")))
    title = escape(str(row.get("title", "")))
    return (
        f'<button type="button" data-approval-action="approve" data-approval-id="{approval_id}" data-approval-title="{title}">Approve</button>'
        f'<button type="button" data-approval-action="reject" data-approval-id="{approval_id}" data-approval-title="{title}">Reject</button>'
    )
