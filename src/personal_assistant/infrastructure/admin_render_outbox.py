"""Outbox section renderer with operator reconciliation actions for the admin dashboard."""

from __future__ import annotations

from html import escape
from typing import Any

from personal_assistant.infrastructure.admin_render_helpers import (
    _format_cell,
)

_OUTBOX_COLUMNS = [
    "created_at",
    "id",
    "event_type",
    "status",
    "attempts",
    "idempotency_key",
    "claim_owner",
    "event_data",
]

_OUTBOX_ACTIONS_SCRIPT = """
<script>
(() => {
  const tokenInput = document.querySelector("[data-admin-token]");
  const status = document.getElementById("outbox-action-status");
  const section = document.getElementById("outbox");
  if (!tokenInput || !status || !section) return;
  let token = "";
  tokenInput.addEventListener("input", () => {
    token = tokenInput.value;
  });
  const showStatus = (message) => {
    status.textContent = message;
  };
  section.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-outbox-action]");
    if (!button) return;
    const messageId = button.dataset.outboxId || "";
    const resolution = button.dataset.outboxAction || "";
    if (!messageId || !resolution) return;
    if (!window.confirm(`Confirm resolution "${resolution}" for message "${messageId}"?`)) return;
    if (!token) {
      showStatus("Enter the admin bearer token to resolve deliveries.");
      return;
    }
    try {
      const response = await fetch(
        `/v1/runtime/outbox/${encodeURIComponent(messageId)}/resolve`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ resolution }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        const resultingStatus = body.status || (resolution === "delivered" ? "published" : "pending");
        showStatus(`Message ${messageId}: ${resultingStatus}`);
        const row = button.closest("tr");
        if (row) {
          const statusCell = row.querySelector("[data-outbox-status]");
          if (statusCell) statusCell.textContent = String(resultingStatus);
          for (const action of row.querySelectorAll("[data-outbox-action]")) action.remove();
        }
        return;
      }
      const detail = body?.error?.message || body?.detail || `request failed (${response.status})`;
      showStatus(`Message ${messageId}: ${detail}`);
    } catch {
      showStatus(`Message ${messageId}: request network error`);
    }
  });
})();
</script>
""".strip()


def _render_outbox(outbox: dict[str, Any]) -> str:
    """Render the outbox section with reconciliation action buttons and script."""
    return "\n".join(
        [
            '<section id="outbox">',
            '<div class="section-heading">',
            "<h2>Outbox</h2>",
            '<p class="section-note">Deliveries in uncertain state can be resolved as delivered or scheduled for retry.</p>',
            "</div>",
            '<p id="outbox-action-status" class="section-note" role="status" aria-live="polite"></p>',
            _render_outbox_table(outbox["items"]),
            "</section>",
            _OUTBOX_ACTIONS_SCRIPT,
        ]
    )


def _render_outbox_table(rows: list[dict[str, Any]]) -> str:
    labels = {column: column.replace("_", " ").title() for column in _OUTBOX_COLUMNS}
    labels["actions"] = "Actions"
    header = "".join(
        f'<th scope="col">{escape(labels[column])}</th>'
        for column in [*_OUTBOX_COLUMNS, "actions"]
    )
    if not rows:
        body = f'<tr><td colspan="{len(_OUTBOX_COLUMNS) + 1}" class="empty">No rows</td></tr>'
    else:
        body = "\n".join(_render_outbox_row(row, labels) for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _render_outbox_row(row: dict[str, Any], labels: dict[str, str]) -> str:
    cells = []
    for column in _OUTBOX_COLUMNS:
        marker = " data-outbox-status" if column == "status" else ""
        cells.append(
            f'<td data-label="{escape(labels[column])}"{marker}>{_format_cell(row.get(column, ""))}</td>'
        )
    cells.append(f'<td data-label="Actions">{_outbox_actions_cell(row)}</td>')
    return "<tr>" + "".join(cells) + "</tr>"


def _outbox_actions_cell(row: dict[str, Any]) -> str:
    if row.get("status") != "uncertain":
        return ""
    message_id = escape(str(row.get("id", "")))
    return (
        f'<button type="button" data-outbox-action="delivered" data-outbox-id="{message_id}">Resolve Delivered</button>'
        f'<button type="button" data-outbox-action="retry" data-outbox-id="{message_id}">Resolve Retry</button>'
    )
