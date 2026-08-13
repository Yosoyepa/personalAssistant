"""Static CSS and JavaScript assets embedded in the admin dashboard page."""

from __future__ import annotations

_ERROR_FILTER_SCRIPT = """
<script>
(() => {
  const filters = document.querySelector("[data-error-filters]");
  if (!filters) return;
  const rows = [...document.querySelectorAll("[data-trace-error-row]")];
  const apply = () => {
    const category = filters.querySelector('[data-error-filter="category"]')?.value || "all";
    const runId = filters.querySelector('[data-error-filter="run_id"]')?.value.trim() || "";
    for (const row of rows) {
      const matchesCategory = category === "all" || row.dataset.category === category;
      const matchesRun = runId === "" || row.dataset.runId.includes(runId);
      row.hidden = !(matchesCategory && matchesRun);
    }
  };
  filters.addEventListener("input", apply);
  filters.addEventListener("change", apply);
})();
</script>
""".strip()


_APPROVAL_ACTIONS_SCRIPT = """
<script>
(() => {
  const tokenInput = document.querySelector("[data-admin-token]");
  const status = document.getElementById("approval-action-status");
  const section = document.getElementById("approvals");
  if (!tokenInput || !status || !section) return;
  let token = "";
  tokenInput.addEventListener("input", () => {
    token = tokenInput.value;
  });
  const showStatus = (message) => {
    status.textContent = message;
  };
  section.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-approval-action]");
    if (!button) return;
    const approvalId = button.dataset.approvalId || "";
    const title = button.dataset.approvalTitle || "";
    const decision = button.dataset.approvalAction;
    if (!approvalId || !decision) return;
    if (!window.confirm(`Confirm ${decision} for "${title}" (${approvalId})?`)) return;
    if (!token) {
      showStatus("Enter the admin bearer token to resolve approvals.");
      return;
    }
    try {
      const response = await fetch(
        `/v1/runtime/approvals/${encodeURIComponent(approvalId)}/${decision}`,
        { method: "POST", headers: { Authorization: `Bearer ${token}` } },
      );
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        showStatus(`Approval ${approvalId}: ${body.status}`);
        const row = button.closest("tr");
        if (row) {
          const statusCell = row.querySelector("[data-approval-status]");
          if (statusCell) statusCell.textContent = String(body.status || "");
          for (const action of row.querySelectorAll("[data-approval-action]")) action.remove();
        }
        return;
      }
      const detail = body?.error?.message || body?.detail || `request failed (${response.status})`;
      if (response.status === 404) {
        showStatus(`Approval ${approvalId}: approval not found`);
        return;
      }
      if (response.status === 409) {
        // The API sanitizes conflict messages; derive the specific reason from
        // the fresh server-side status instead of trusting the stale page.
        try {
          const listResponse = await fetch("/v1/runtime/approvals", {
            headers: { Authorization: `Bearer ${token}` },
          });
          const approvals = listResponse.ok ? await listResponse.json() : [];
          const current = Array.isArray(approvals)
            ? approvals.find((item) => item.approval_id === approvalId)
            : null;
          const currentStatus = current?.status || "";
          if (currentStatus === "approved" || currentStatus === "rejected") {
            showStatus(`Approval ${approvalId}: approval was already ${currentStatus}`);
            const row = button.closest("tr");
            if (row) {
              const statusCell = row.querySelector("[data-approval-status]");
              if (statusCell) statusCell.textContent = currentStatus;
              for (const action of row.querySelectorAll("[data-approval-action]")) action.remove();
            }
            return;
          }
        } catch {
          // Fall through to the sanitized message below.
        }
      }
      showStatus(`Approval ${approvalId}: ${detail}`);
    } catch {
      showStatus(`Approval ${approvalId}: network error, page unchanged`);
    }
  });
})();
</script>
""".strip()


_CSS = """
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --panel: #ffffff;
  --text: #1b1f24;
  --muted: #5f6b7a;
  --line: #d8dee8;
  --line-strong: #b8c2cf;
  --ok: #1f7a4d;
  --attention: #9a5b00;
  --danger: #b42318;
  --link: #1457a8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.container {
  width: min(1220px, calc(100% - 32px));
  margin: 0 auto;
}
.topbar {
  background: #ffffff;
  border-bottom: 1px solid var(--line);
  padding: 18px 0 14px;
}
h1, h2, h3, p { margin: 0; }
h1 { font-size: 22px; font-weight: 700; }
h2 { font-size: 18px; }
h3 { font-size: 14px; margin: 16px 0 8px; }
.muted { color: var(--muted); margin-top: 4px; }
nav {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 14px;
}
nav a {
  color: var(--link);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  padding: 2px 0;
}
nav a:hover { border-bottom-color: currentColor; }
main { padding: 8px 0 36px; }
section {
  border-top: 1px solid var(--line);
  padding: 22px 0;
}
main section:first-child {
  border-top: 0;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.status {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  border-radius: 6px;
  padding: 3px 8px;
  font-weight: 700;
  border: 1px solid currentColor;
}
.status-ok { color: var(--ok); }
.status-needs_attention { color: var(--attention); }
.section-note {
  color: var(--muted);
  font-size: 13px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 0 0 14px;
}
.summary-card {
  min-width: 0;
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 4px solid var(--line-strong);
  border-radius: 8px;
  padding: 11px 12px;
}
.summary-card-ok { border-left-color: var(--ok); }
.summary-card-attention { border-left-color: var(--attention); }
.summary-card-danger { border-left-color: var(--danger); }
.summary-label,
.summary-detail {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.summary-value {
  display: block;
  margin: 4px 0;
  font-size: 20px;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
  margin: 0 0 12px;
}
.filters label {
  display: grid;
  gap: 4px;
  color: var(--muted);
  font-size: 12px;
}
.filters input,
.filters select {
  min-height: 32px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  padding: 5px 8px;
  background: #ffffff;
  color: var(--text);
  font: inherit;
}
.filters input { min-width: 260px; }
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
}
table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  min-width: 780px;
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 7px 8px;
  text-align: left;
  vertical-align: top;
  word-break: break-word;
}
th {
  background: #edf1f5;
  color: #323a45;
  font-size: 12px;
  letter-spacing: 0;
  text-transform: uppercase;
}
td {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
tbody tr:last-child td { border-bottom: 0; }
.empty {
  color: var(--muted);
  text-align: center;
  font-family: inherit;
}
@media (max-width: 720px) {
  .container { width: min(100% - 20px, 1220px); }
  .topbar { padding: 14px 0 10px; }
  .summary-grid { grid-template-columns: 1fr; }
  .table-wrap {
    overflow: visible;
    border: 0;
    background: transparent;
  }
  table,
  thead,
  tbody,
  tr,
  th,
  td {
    display: block;
    width: 100%;
    min-width: 0;
  }
  thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
  tr {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    margin-bottom: 10px;
    overflow: hidden;
  }
  td {
    display: grid;
    grid-template-columns: minmax(92px, 34%) minmax(0, 1fr);
    gap: 10px;
    border-bottom: 1px solid var(--line);
  }
  td::before {
    content: attr(data-label);
    color: var(--muted);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
  }
  td.empty {
    display: block;
  }
  td.empty::before {
    content: "";
  }
}
""".strip()
