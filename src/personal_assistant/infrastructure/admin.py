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
from personal_assistant.application.ports.calendar import CalendarEventResult
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
        agenda = self.agenda(principal, now=now, limit=safe_limit)
        reminders = self.reminders(principal, now=now, limit=safe_limit)
        events = self.events(principal, limit=safe_limit)
        states = self.states(principal, limit=safe_limit)
        memory = self.memory(principal, limit=safe_limit)
        approvals = self.approvals(principal, limit=safe_limit)
        errors = self.errors(principal, limit=safe_limit)
        health = self.health(
            traces=traces,
            outbox=outbox,
            scheduler=scheduler,
            agenda=agenda,
            reminders=reminders,
            events=events,
            states=states,
            memory=memory,
            approvals=approvals,
            errors=errors,
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
            "agenda": agenda,
            "reminders": reminders,
            "events": events,
            "states": states,
            "memory": memory,
            "errors": errors,
        }

    def health(
        self,
        *,
        traces: dict[str, Any],
        outbox: dict[str, Any],
        scheduler: dict[str, Any],
        agenda: dict[str, Any],
        reminders: dict[str, Any],
        events: dict[str, Any],
        states: dict[str, Any],
        memory: dict[str, Any],
        approvals: dict[str, Any],
        errors: dict[str, Any],
    ) -> dict[str, Any]:
        outbox_counts = outbox["counts"]
        state_counts = states["counts"]
        scheduler_counts = scheduler["counts"]
        attention = {
            "pending_approvals": approvals["pending_count"],
            "due_reminders": scheduler_counts["due"],
            "errors": errors["total"],
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
            },
        }

    def traces(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        safe_limit = clamp_limit(limit)
        events = sorted(
            self.container.traces.list_for_tenant(principal),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        error_events = _trace_error_events(events)
        items = [_trace_item(event) for event in events[:safe_limit]]
        return {
            "total": len(events),
            "run_count": len({event.run_id for event in events}),
            "counts": dict(Counter(event.event_type.value for event in events)),
            "error_count": len(error_events),
            "error_category_counts": dict(Counter(_trace_error_category(event) for event in error_events)),
            "error_runs": _trace_error_runs(error_events, limit=safe_limit),
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
        jobs = _ordered_reminder_jobs(_tenant_scheduler_jobs(self.container, principal.tenant_id), now=now)
        due = [job for job in jobs if _reminder_due(job, now)]
        counts = {
            "scheduled": len(jobs),
            "due": len(due),
            "sent": len([job for job in jobs if job.sent]),
            "pending": len([job for job in jobs if not job.sent and not _reminder_due(job, now)]),
        }
        return {
            "total": len(jobs),
            "counts": counts,
            "items": [_scheduled_reminder_item(job, now=now) for job in jobs[: clamp_limit(limit)]],
        }

    def agenda(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        events = _ordered_agenda_events(_tenant_calendar_events(self.container, principal), now=now)
        upcoming = [event for event in events if _is_on_or_after(event.starts_at, now)]
        past = [event for event in events if not _is_on_or_after(event.starts_at, now)]
        today = [event for event in events if _is_same_day(event.starts_at, now)]
        return {
            "total": len(events),
            "upcoming_count": len(upcoming),
            "today_count": len(today),
            "past_count": len(past),
            "next_event": _agenda_item(upcoming[0], now=now) if upcoming else None,
            "items": [_agenda_item(event, now=now) for event in events[: clamp_limit(limit)]],
        }

    def reminders(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        jobs = _ordered_reminder_jobs(_tenant_scheduler_jobs(self.container, principal.tenant_id), now=now)
        events_by_id = {event.event_id: event for event in _tenant_calendar_events(self.container, principal)}
        due = [job for job in jobs if _reminder_due(job, now)]
        counts = {
            "scheduled": len(jobs),
            "due": len(due),
            "sent": len([job for job in jobs if job.sent]),
            "pending": len([job for job in jobs if not job.sent and not _reminder_due(job, now)]),
        }
        return {
            "total": len(jobs),
            "counts": counts,
            "items": [
                _reminder_item(job, now=now, event=events_by_id.get(job.calendar_event_id))
                for job in jobs[: clamp_limit(limit)]
            ],
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
            self.container.traces.list_for_tenant(principal),
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

    def errors(
        self,
        principal: Principal,
        *,
        category: str | None = None,
        run_id: str | None = None,
        event_type: str | TraceEventType | None = None,
        source: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        safe_limit = clamp_limit(limit)
        trace_events = _filter_trace_errors(
            _trace_error_events(self.container.traces.list_for_tenant(principal)),
            category=category,
            run_id=run_id,
            event_type=event_type,
        )
        traces = [
            _trace_error_item(trace)
            for trace in trace_events
        ]
        failed_states = [
            _workflow_error_item(state)
            for state in self.container.states.list_for_tenant(principal)
            if state.status == WorkflowStatus.failed
        ]
        failed_outbox = [
            _outbox_error_item(message)
            for message in _tenant_outbox_messages(self.container, principal.tenant_id)
            if message.dispatch_status == OutboxStatus.failed
        ]
        items = [
            item
            for item in [*traces, *failed_states, *failed_outbox]
            if _error_item_matches_filters(item, category=category, run_id=run_id, event_type=event_type, source=source)
        ]
        items = sorted(
            items,
            key=lambda item: item["timestamp"] or "",
            reverse=True,
        )
        return {
            "total": len(items),
            "counts": dict(Counter(item["source"] for item in items)),
            "category_counts": dict(Counter(item["category"] for item in items)),
            "trace_category_counts": dict(Counter(_trace_error_category(event) for event in trace_events)),
            "event_type_counts": dict(Counter(event.event_type.value for event in trace_events)),
            "run_count": len({event.run_id for event in trace_events}),
            "runs": _trace_error_runs(trace_events, limit=safe_limit),
            "filters": {
                "category": _normalized_filter(category),
                "run_id": _normalized_filter(run_id),
                "event_type": _event_type_value(event_type),
                "source": _normalized_filter(source),
            },
            "items": items[:safe_limit],
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
            '<a href="#agenda">Agenda</a>',
            '<a href="#reminders">Reminders</a>',
            '<a href="#errors">Errors</a>',
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
            _render_agenda(snapshot["agenda"]),
            _render_reminders(snapshot["reminders"]),
            _render_errors(snapshot["errors"]),
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


def _render_agenda(agenda: dict[str, Any]) -> str:
    next_event = agenda.get("next_event")
    cards = [
        {
            "label": "Calendar events",
            "value": agenda["total"],
            "detail": f'{agenda["upcoming_count"]} upcoming, {agenda["past_count"]} past',
        },
        {
            "label": "Today",
            "value": agenda["today_count"],
            "detail": "events matching generated date",
        },
        {
            "label": "Next event",
            "value": next_event["title"] if next_event else "None",
            "detail": next_event["starts_at"] if next_event else "No upcoming event",
            "tone": "ok" if next_event else "neutral",
        },
    ]
    return "\n".join(
        [
            '<section id="agenda">',
            '<div class="section-heading">',
            "<h2>Agenda</h2>",
            '<p class="section-note">Calendar events ordered by next action.</p>',
            "</div>",
            _render_summary_cards(cards),
            _render_table(
                ["starts_at", "status", "title", "event_id", "idempotency_key"],
                agenda["items"],
            ),
            "</section>",
        ]
    )


def _render_reminders(reminders: dict[str, Any]) -> str:
    counts = reminders["counts"]
    cards = [
        {
            "label": "Due",
            "value": counts.get("due", 0),
            "detail": "unsent reminders at or before now",
            "tone": "attention" if counts.get("due", 0) else "neutral",
        },
        {
            "label": "Pending",
            "value": counts.get("pending", 0),
            "detail": "scheduled for later",
        },
        {
            "label": "Sent",
            "value": counts.get("sent", 0),
            "detail": "already dispatched",
        },
        {
            "label": "Total",
            "value": reminders["total"],
            "detail": "scheduled reminder jobs",
        },
    ]
    return "\n".join(
        [
            '<section id="reminders">',
            '<div class="section-heading">',
            "<h2>Reminders</h2>",
            '<p class="section-note">Due and pending notifications are listed before sent jobs.</p>',
            "</div>",
            _render_summary_cards(cards),
            _render_table(
                [
                    "notify_at",
                    "status",
                    "event_title",
                    "event_starts_at",
                    "reminder_id",
                    "channel",
                    "recipient",
                    "body_preview",
                ],
                reminders["items"],
            ),
            "</section>",
        ]
    )


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
    return "\n".join(
        [
            '<div class="filters" data-error-filters>',
            '<label>Category <select data-error-filter="category">',
            category_options,
            "</select></label>",
            '<label>Run ID <input type="search" data-error-filter="run_id" placeholder="run id"></label>',
            "</div>",
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


def _tenant_outbox_messages(container: AppContainer, tenant_id: str) -> list[OutboxMessage]:
    principal = local_admin_principal(tenant_id=tenant_id)
    return container.outbox.list_for_tenant(principal)


def _tenant_scheduler_jobs(container: AppContainer, tenant_id: str) -> list[ScheduledReminder]:
    principal = local_admin_principal(tenant_id=tenant_id)
    return container.scheduler.list_for_tenant(principal)


def _tenant_calendar_events(container: AppContainer, principal: Principal) -> list[CalendarEventResult]:
    calendar = getattr(container, "calendar", None)
    if calendar is None:
        return []
    list_events = getattr(calendar, "list_events", None)
    if not callable(list_events):
        return []
    return list_events(principal)


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


def _trace_error_events(events: list[TraceEvent]) -> list[TraceEvent]:
    return [event for event in events if event.error or event.event_type == TraceEventType.agent_failed]


def _filter_trace_errors(
    events: list[TraceEvent],
    *,
    category: str | None,
    run_id: str | None,
    event_type: str | TraceEventType | None,
) -> list[TraceEvent]:
    normalized_category = _normalized_filter(category)
    normalized_run_id = _normalized_filter(run_id)
    normalized_event_type = _event_type_value(event_type)
    return [
        event
        for event in events
        if (normalized_category is None or _trace_error_category(event) == normalized_category)
        and (normalized_run_id is None or event.run_id == normalized_run_id)
        and (normalized_event_type is None or event.event_type.value == normalized_event_type)
    ]


def _trace_error_runs(events: list[TraceEvent], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[TraceEvent]] = {}
    for event in events:
        grouped.setdefault(event.run_id, []).append(event)

    rows: list[dict[str, Any]] = []
    for run_id, run_events in grouped.items():
        sorted_events = sorted(run_events, key=lambda event: event.timestamp, reverse=True)
        latest = sorted_events[0]
        rows.append(
            {
                "latest_at": _iso(latest.timestamp),
                "first_at": _iso(sorted_events[-1].timestamp),
                "run_id": run_id,
                "count": len(sorted_events),
                "categories": dict(Counter(_trace_error_category(event) for event in sorted_events)),
                "event_types": dict(Counter(event.event_type.value for event in sorted_events)),
                "last_message": _trace_error_message(latest),
                "trace_ids": [event.trace_id for event in sorted_events],
            }
        )
    rows.sort(key=lambda row: str(row["latest_at"] or ""), reverse=True)
    return rows[: clamp_limit(limit)]


def _trace_error_category(event: TraceEvent) -> str:
    explicit = _trace_error_explicit_category(event)
    if explicit is not None:
        return explicit

    tool_name = _lower_text(event.tool_call.get("name"))
    run_id = event.run_id.lower()
    model = (event.model or "").lower()
    input_keys = {str(key).lower() for key in event.input_summary}
    input_text = " ".join(str(value).lower() for value in event.input_summary.values())
    error_text = json.dumps(event.error, default=str, sort_keys=True).lower() if event.error else ""

    if (
        "audio" in tool_name
        or "transcrib" in tool_name
        or "tts" in tool_name
        or "audio" in run_id
        or "transcription" in run_id
        or {"media_kind", "media_mime_type", "media_file_size", "transcription_filename"} & input_keys
        or "audio" in input_text
        or "audio" in error_text
        or "transcrib" in error_text
        or "tts" in error_text
    ):
        return "audio"
    if (
        event.event_type == TraceEventType.llm_called
        or bool(model)
        or {"schema", "prompt_id", "prompt_version"} & input_keys
        or (run_id.startswith("command:") and run_id.endswith(":intent"))
        or "llm" in error_text
        or "model" in error_text
    ):
        return "llm"
    if event.event_type == TraceEventType.tool_called or event.tool_call or "tool" in error_text:
        return "tool"
    if event.event_type == TraceEventType.agent_failed or "workflow" in input_keys or "workflow" in run_id or "workflow" in error_text:
        return "workflow"
    return "unknown"


def _trace_error_explicit_category(event: TraceEvent) -> str | None:
    for key in ("category", "component", "source"):
        value = _lower_text(event.error.get(key))
        for category in ("audio", "llm", "tool", "workflow"):
            if value == category or value.startswith(f"{category}."):
                return category
    return None


def _trace_error_operation(event: TraceEvent) -> str:
    for value in (
        event.tool_call.get("name"),
        event.input_summary.get("schema"),
        event.input_summary.get("prompt_id"),
    ):
        normalized = _string_value(value)
        if normalized:
            return normalized
    return event.event_type.value


def _trace_error_type(event: TraceEvent) -> str:
    for key in ("type", "code", "error_type"):
        value = _string_value(event.error.get(key))
        if value:
            return value
    return event.event_type.value


def _trace_error_message(event: TraceEvent) -> str:
    for key in ("message", "detail", "reason", "code"):
        value = _string_value(event.error.get(key))
        if value:
            return _preview(value, length=240)
    if event.error:
        return _preview(json.dumps(event.error, default=str, sort_keys=True), length=240)
    return ""


def _error_item_matches_filters(
    item: dict[str, Any],
    *,
    category: str | None,
    run_id: str | None,
    event_type: str | TraceEventType | None,
    source: str | None,
) -> bool:
    normalized_category = _normalized_filter(category)
    normalized_run_id = _normalized_filter(run_id)
    normalized_event_type = _event_type_value(event_type)
    normalized_source = _normalized_filter(source)
    return (
        (normalized_category is None or item.get("category") == normalized_category)
        and (normalized_run_id is None or item.get("run_id") == normalized_run_id)
        and (normalized_event_type is None or item.get("event_type") == normalized_event_type)
        and (normalized_source is None or item.get("source") == normalized_source)
    )


def _event_type_value(event_type: str | TraceEventType | None) -> str | None:
    if event_type is None:
        return None
    if isinstance(event_type, TraceEventType):
        return event_type.value
    return _normalized_filter(event_type)


def _normalized_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _lower_text(value: Any) -> str:
    return _string_value(value).lower()


def _error_category_label(category: str) -> str:
    if category == "all":
        return "All"
    if category == "llm":
        return "LLM"
    return category.replace("_", " ").title()


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
    due = _reminder_due(job, now)
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


def _agenda_item(event: CalendarEventResult, *, now: datetime) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": event.title,
        "starts_at": _iso(event.starts_at),
        "idempotency_key": event.idempotency_key,
        "reused": event.reused,
        "status": "upcoming" if _is_on_or_after(event.starts_at, now) else "past",
    }


def _reminder_item(
    job: ScheduledReminder,
    *,
    now: datetime,
    event: CalendarEventResult | None,
) -> dict[str, Any]:
    item = _scheduled_reminder_item(job, now=now)
    item.update(
        {
            "event_title": event.title if event is not None else None,
            "event_starts_at": _iso(event.starts_at) if event is not None else None,
        }
    )
    return item


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


def _trace_error_item(event: TraceEvent) -> dict[str, Any]:
    error_type = _trace_error_type(event)
    return {
        "timestamp": _iso(event.timestamp),
        "source": "trace",
        "category": _trace_error_category(event),
        "event_type": event.event_type.value,
        "operation": _trace_error_operation(event),
        "type": error_type,
        "error_type": error_type,
        "message": _trace_error_message(event),
        "run_id": event.run_id,
        "workflow_id": "",
        "agent_id": event.agent_id,
        "details": _trace_item(event),
    }


def _workflow_error_item(state: WorkflowState) -> dict[str, Any]:
    return {
        "timestamp": _iso(state.updated_at),
        "source": "workflow",
        "category": "workflow",
        "event_type": state.workflow_type,
        "operation": state.step,
        "type": state.workflow_type,
        "message": state.data.get("error", state.step),
        "run_id": "",
        "workflow_id": state.workflow_id,
        "agent_id": "",
        "details": _workflow_state_item(state),
    }


def _outbox_error_item(message: OutboxMessage) -> dict[str, Any]:
    return {
        "timestamp": _iso(message.next_attempt_at or message.created_at),
        "source": "outbox",
        "category": "tool",
        "event_type": message.event.type,
        "operation": message.event.type,
        "type": message.event.type,
        "message": message.event.data.get("error", ""),
        "run_id": "",
        "workflow_id": "",
        "agent_id": "",
        "details": _outbox_item(message),
    }


def _model_item(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _preview(text: str, *, length: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= length:
        return normalized
    return f"{normalized[: length - 3]}..."


def _ordered_agenda_events(events: list[CalendarEventResult], *, now: datetime) -> list[CalendarEventResult]:
    upcoming = sorted(
        (event for event in events if _is_on_or_after(event.starts_at, now)),
        key=lambda event: (_datetime_sort_value(event.starts_at), event.event_id),
    )
    past = sorted(
        (event for event in events if not _is_on_or_after(event.starts_at, now)),
        key=lambda event: (_datetime_sort_value(event.starts_at), event.event_id),
        reverse=True,
    )
    return [*upcoming, *past]


def _ordered_reminder_jobs(jobs: list[ScheduledReminder], *, now: datetime) -> list[ScheduledReminder]:
    due = sorted(
        (job for job in jobs if _reminder_due(job, now)),
        key=lambda job: (_datetime_sort_value(job.notify_at), job.reminder_id),
    )
    pending = sorted(
        (job for job in jobs if not job.sent and not _reminder_due(job, now)),
        key=lambda job: (_datetime_sort_value(job.notify_at), job.reminder_id),
    )
    sent = sorted(
        (job for job in jobs if job.sent),
        key=lambda job: (_datetime_sort_value(job.notify_at), job.reminder_id),
        reverse=True,
    )
    return [*due, *pending, *sent]


def _reminder_due(job: ScheduledReminder, now: datetime) -> bool:
    return not job.sent and _is_on_or_before(job.notify_at, now)


def _is_on_or_after(value: datetime, reference: datetime) -> bool:
    return _datetime_sort_value(value) >= _datetime_sort_value(reference)


def _is_on_or_before(value: datetime, reference: datetime) -> bool:
    return _datetime_sort_value(value) <= _datetime_sort_value(reference)


def _is_same_day(value: datetime, reference: datetime) -> bool:
    reference_tz = reference.tzinfo or UTC
    return _aware_datetime(value).astimezone(reference_tz).date() == _aware_datetime(reference).date()


def _datetime_sort_value(value: datetime) -> float:
    return _aware_datetime(value).timestamp()


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


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
