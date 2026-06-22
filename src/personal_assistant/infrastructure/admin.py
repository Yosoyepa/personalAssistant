"""Read-only local admin dashboard for the in-memory runtime."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from html import escape
from ipaddress import ip_address, ip_network
from typing import Any

from personal_assistant.application.dto.events import OutboxMessage, OutboxStatus
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.memory.models import MemoryRecord
from personal_assistant.infrastructure.bootstrap import AppContainer


DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_LOCAL_NETWORKS = (
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
)


def local_admin_principal(
    *,
    tenant_id: str,
    principal_id: str = "local-admin",
    permission_tier: PermissionTier = PermissionTier.P0,
) -> Principal:
    """Create the trusted principal used by the local-only admin boundary."""
    principal = Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        auth_subject=principal_id,
        auth_provider="local-admin",
        permission_tier=permission_tier,
    )
    principal.mark_trusted("local-admin")
    return principal


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def is_local_client(host: str | None) -> bool:
    """Return true only for loopback clients used by the local admin UI."""
    normalized = _normalize_host(host)
    if normalized is None:
        return False
    if normalized == "localhost":
        return True
    try:
        client_ip = ip_address(normalized)
    except ValueError:
        return False
    return any(client_ip in network for network in _LOCAL_NETWORKS)


class AdminDashboard:
    """Builds local dashboard snapshots from the composed application container."""

    def __init__(self, container: AppContainer) -> None:
        self.container = container

    def snapshot(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        safe_limit = clamp_limit(limit)
        traces = self.traces(principal, limit=safe_limit)
        outbox = self.outbox(principal, limit=safe_limit)
        scheduler = self.scheduler(principal, now=now, limit=safe_limit)
        events = self.events(principal, limit=safe_limit)
        states = self.states(principal, limit=safe_limit)
        memory = self.memory(principal, limit=safe_limit)
        approvals = self.approvals(principal, limit=safe_limit)
        health = self.health(
            traces=traces,
            outbox=outbox,
            scheduler=scheduler,
            events=events,
            states=states,
            memory=memory,
            approvals=approvals,
        )

        return {
            "meta": {
                "generated_at": _iso(now),
                "tenant_id": principal.tenant_id,
                "principal_id": principal.principal_id,
                "local_only": True,
                "version": "admin.v1",
            },
            "health": health,
            "approvals": approvals,
            "traces": traces,
            "outbox": outbox,
            "scheduler": scheduler,
            "events": events,
            "states": states,
            "memory": memory,
        }

    def health(
        self,
        *,
        traces: dict[str, Any],
        outbox: dict[str, Any],
        scheduler: dict[str, Any],
        events: dict[str, Any],
        states: dict[str, Any],
        memory: dict[str, Any],
        approvals: dict[str, Any],
    ) -> dict[str, Any]:
        outbox_counts = outbox["counts"]
        state_counts = states["counts"]
        attention = {
            "pending_approvals": approvals["pending_count"],
            "due_reminders": scheduler["counts"]["due"],
            "pending_outbox": outbox_counts.get(OutboxStatus.pending.value, 0),
            "claimed_outbox": outbox_counts.get(OutboxStatus.claimed.value, 0),
            "failed_outbox": outbox_counts.get(OutboxStatus.failed.value, 0),
            "failed_workflows": state_counts.get(WorkflowStatus.failed.value, 0),
        }
        status = "needs_attention" if any(attention.values()) else "ok"
        return {
            "status": status,
            "attention": attention,
            "components": {
                "traces": {"status": "ok", "total": traces["total"], "runs": traces["run_count"]},
                "outbox": {"status": "ok", "total": outbox["total"], "counts": outbox_counts},
                "scheduler": {"status": "ok", "total": scheduler["total"], "counts": scheduler["counts"]},
                "events": {"status": "ok", "total": events["total"]},
                "states": {"status": "ok", "total": states["total"], "counts": state_counts},
                "memory": {"status": "ok", "total": memory["total"], "confirmed": memory["confirmed_count"]},
            },
        }

    def traces(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        events = sorted(
            self.container.traces.list_for_tenant(principal.tenant_id),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        items = [_trace_item(event) for event in events[: clamp_limit(limit)]]
        return {
            "total": len(events),
            "run_count": len({event.run_id for event in events}),
            "counts": dict(Counter(event.event_type.value for event in events)),
            "items": items,
        }

    def outbox(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        messages = sorted(
            _tenant_outbox_messages(self.container, principal.tenant_id),
            key=lambda message: message.created_at,
            reverse=True,
        )
        return {
            "total": len(messages),
            "counts": dict(Counter(message.dispatch_status.value for message in messages)),
            "items": [_outbox_item(message) for message in messages[: clamp_limit(limit)]],
        }

    def scheduler(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        jobs = sorted(
            _tenant_scheduler_jobs(self.container, principal.tenant_id),
            key=lambda job: job.notify_at,
            reverse=True,
        )
        due = [job for job in jobs if not job.sent and job.notify_at <= now]
        counts = {
            "scheduled": len(jobs),
            "due": len(due),
            "sent": len([job for job in jobs if job.sent]),
            "pending": len([job for job in jobs if not job.sent and job.notify_at > now]),
        }
        return {
            "total": len(jobs),
            "counts": counts,
            "items": [_scheduled_reminder_item(job, now=now) for job in jobs[: clamp_limit(limit)]],
        }

    def events(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        events = sorted(
            self.container.event_store.list_for_tenant(principal),
            key=lambda event: event.time,
            reverse=True,
        )
        return {
            "total": len(events),
            "counts": dict(Counter(event.type for event in events)),
            "items": [_model_item(event) for event in events[: clamp_limit(limit)]],
        }

    def states(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        states = sorted(
            self.container.states.list_for_tenant(principal),
            key=lambda state: state.updated_at,
            reverse=True,
        )
        return {
            "total": len(states),
            "counts": dict(Counter(state.status.value for state in states)),
            "items": [_workflow_state_item(state) for state in states[: clamp_limit(limit)]],
        }

    def memory(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        records = sorted(
            self.container.memory.list_for_tenant(principal),
            key=lambda record: record.created_at,
            reverse=True,
        )
        return {
            "total": len(records),
            "confirmed_count": len([record for record in records if record.confirmed]),
            "counts": dict(Counter(record.kind.value for record in records)),
            "items": [_memory_item(record) for record in records[: clamp_limit(limit)]],
        }

    def approvals(
        self,
        principal: Principal,
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        raw_states = sorted(
            self.container.states.list_for_tenant(principal),
            key=lambda state: state.updated_at,
            reverse=True,
        )
        raw_traces = sorted(
            self.container.traces.list_for_tenant(principal.tenant_id),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        waiting_states = [_workflow_state_item(state) for state in raw_states if state.status == WorkflowStatus.waiting_approval]
        approval_traces = [_trace_item(trace) for trace in raw_traces if trace.event_type == TraceEventType.approval_requested]
        return {
            "pending_count": len(waiting_states),
            "workflow_states": waiting_states[: clamp_limit(limit)],
            "trace_events": approval_traces[: clamp_limit(limit)],
        }

    def render_html(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        return render_dashboard_html(self.snapshot(principal, now=now, limit=limit))


def render_dashboard_html(snapshot: dict[str, Any]) -> str:
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
            '<header class="topbar">',
            '<div class="container">',
            "<h1>Personal Assistant Admin</h1>",
            f'<p class="muted">Tenant {escape(meta["tenant_id"])} | Principal {escape(meta["principal_id"])} | {escape(meta["generated_at"])}</p>',
            '<nav aria-label="Dashboard sections">',
            '<a href="#health">Health</a>',
            '<a href="#approvals">Approvals</a>',
            '<a href="#traces">Traces</a>',
            '<a href="#outbox">Outbox</a>',
            '<a href="#scheduler">Scheduler</a>',
            '<a href="#events">Events</a>',
            '<a href="#states">States</a>',
            '<a href="#memory">Memory</a>',
            "</nav>",
            "</div>",
            "</header>",
            '<main class="container">',
            _render_health(health),
            _render_table_section(
                "approvals",
                "Approvals",
                ["workflow_id", "workflow_type", "status", "step", "idempotency_key", "updated_at", "data"],
                snapshot["approvals"]["workflow_states"],
            ),
            _render_table_section(
                "traces",
                "Traces",
                ["timestamp", "run_id", "event_type", "agent_id", "tool_call", "validation", "error"],
                snapshot["traces"]["items"],
            ),
            _render_table_section(
                "outbox",
                "Outbox",
                ["created_at", "id", "event_type", "status", "attempts", "idempotency_key", "claim_owner", "event_data"],
                snapshot["outbox"]["items"],
            ),
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


def _render_health(health: dict[str, Any]) -> str:
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
    return "\n".join(
        [
            '<section id="health">',
            '<div class="section-heading">',
            "<h2>Health</h2>",
            f'<span class="status status-{escape(health["status"])}">{escape(health["status"])}</span>',
            "</div>",
            _render_table(["component", "status", "total", "details"], component_rows),
            '<h3>Attention</h3>',
            _render_table(["signal", "count"], attention_rows),
            "</section>",
        ]
    )


def _render_table_section(section_id: str, title: str, columns: list[str], rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            f'<section id="{escape(section_id)}">',
            f"<h2>{escape(title)}</h2>",
            _render_table(columns, rows),
            "</section>",
        ]
    )


def _render_table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    header = "".join(f"<th>{escape(column.replace('_', ' ').title())}</th>" for column in columns)
    if not rows:
        body = f'<tr><td colspan="{len(columns)}" class="empty">No rows</td></tr>'
    else:
        body = "\n".join(
            "<tr>"
            + "".join(f"<td>{_format_cell(row.get(column, ''))}</td>" for column in columns)
            + "</tr>"
            for row in rows
        )
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _format_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str, sort_keys=True)
    elif isinstance(value, bool):
        value = "yes" if value else "no"
    elif value is None:
        value = ""
    return escape(str(value))


def _tenant_outbox_messages(container: AppContainer, tenant_id: str) -> list[OutboxMessage]:
    messages = getattr(container.outbox, "_messages_by_key", {})
    return [
        message
        for key, message in messages.items()
        if isinstance(key, tuple) and key[0] == tenant_id
    ]


def _tenant_scheduler_jobs(container: AppContainer, tenant_id: str) -> list[ScheduledReminder]:
    jobs = getattr(container.scheduler, "_jobs_by_key", {})
    return [
        job
        for key, job in jobs.items()
        if isinstance(key, tuple) and key[0] == tenant_id
    ]


def _trace_item(event: TraceEvent) -> dict[str, Any]:
    return {
        "trace_id": event.trace_id,
        "run_id": event.run_id,
        "agent_id": event.agent_id,
        "event_type": event.event_type.value,
        "timestamp": _iso(event.timestamp),
        "input_summary": event.input_summary,
        "context_refs": event.context_refs,
        "tool_call": event.tool_call,
        "model": event.model,
        "output_summary": event.output_summary,
        "validation": event.validation,
        "error": event.error,
        "parent_event_id": event.parent_event_id,
    }


def _outbox_item(message: OutboxMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "tenant_id": message.tenant_id,
        "event_id": message.event.id,
        "event_type": message.event.type,
        "event_subject": message.event.subject,
        "event_data": message.event.data,
        "idempotency_key": message.idempotency_key,
        "status": message.dispatch_status.value,
        "claim_owner": message.claim_owner,
        "claimed_until": _iso(message.claimed_until),
        "next_attempt_at": _iso(message.next_attempt_at),
        "attempts": message.attempts,
        "created_at": _iso(message.created_at),
        "published_at": _iso(message.published_at),
    }


def _scheduled_reminder_item(job: ScheduledReminder, *, now: datetime) -> dict[str, Any]:
    sent = bool(job.sent)
    due = not sent and job.notify_at <= now
    return {
        "reminder_id": job.reminder_id,
        "tenant_id": job.tenant_id,
        "calendar_event_id": job.calendar_event_id,
        "notify_at": _iso(job.notify_at),
        "channel": job.channel,
        "recipient": job.recipient,
        "body_preview": _preview(job.body),
        "idempotency_key": job.idempotency_key,
        "sent": sent,
        "due": due,
        "status": "sent" if sent else "due" if due else "scheduled",
    }


def _workflow_state_item(state: WorkflowState) -> dict[str, Any]:
    return {
        "workflow_id": state.workflow_id,
        "tenant_id": state.tenant_id,
        "workflow_type": state.workflow_type,
        "status": state.status.value,
        "step": state.step,
        "idempotency_key": state.idempotency_key,
        "data": state.data,
        "created_at": _iso(state.created_at),
        "updated_at": _iso(state.updated_at),
    }


def _memory_item(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "kind": record.kind.value,
        "text_preview": _preview(record.text),
        "source": record.source,
        "confirmed": record.confirmed,
        "created_at": _iso(record.created_at),
    }


def _model_item(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _preview(text: str, *, length: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= length:
        return normalized
    return f"{normalized[: length - 3]}..."


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _normalize_host(host: str | None) -> str | None:
    if host is None:
        return None
    value = host.strip().lower()
    if not value:
        return None
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    if value.count(":") == 1:
        host_part, port_part = value.rsplit(":", 1)
        if port_part.isdigit():
            return host_part
    return value


_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1b1f24;
  --muted: #5a6472;
  --line: #d9dee7;
  --ok: #157347;
  --attention: #a15c00;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.container {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
}
.topbar {
  background: #ffffff;
  border-bottom: 1px solid var(--line);
  padding: 18px 0 14px;
}
h1, h2, h3, p { margin: 0; }
h1 { font-size: 22px; font-weight: 700; }
h2 { font-size: 18px; margin-bottom: 10px; }
h3 { font-size: 14px; margin: 16px 0 8px; }
.muted { color: var(--muted); margin-top: 4px; }
nav {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}
nav a {
  color: #174ea6;
  text-decoration: none;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 5px 9px;
  background: #fdfefe;
}
main { padding: 18px 0 36px; }
section {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
  margin: 0 0 14px;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
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
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
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
  background: #eef1f5;
  color: #323a45;
  font-size: 12px;
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
  .container { width: min(100% - 20px, 1180px); }
  section { padding: 12px; }
  table { min-width: 680px; }
}
""".strip()
