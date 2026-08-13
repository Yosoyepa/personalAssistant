"""Admin dashboard facade: snapshot assembly over the per-section fetchers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from personal_assistant.application.dto.tracing import TraceEventType
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin_auth import DEFAULT_LIMIT, clamp_limit
from personal_assistant.infrastructure.admin_data_approvals import fetch_approvals
from personal_assistant.infrastructure.admin_data_context import (
    fetch_context,
    fetch_guardrail_metrics,
)
from personal_assistant.infrastructure.admin_data_errors import fetch_errors
from personal_assistant.infrastructure.admin_data_health import fetch_health
from personal_assistant.infrastructure.admin_data_schedule import (
    fetch_agenda,
    fetch_reminders,
    fetch_scheduler,
)
from personal_assistant.infrastructure.admin_data_stores import (
    fetch_delivery_counts,
    fetch_events,
    fetch_memory,
    fetch_outbox,
    fetch_states,
    fetch_traces,
)
from personal_assistant.infrastructure.admin_render import render_dashboard_html
from personal_assistant.infrastructure.admin_time import _iso
from personal_assistant.infrastructure.bootstrap import AppContainer


def fetch_snapshot(
    container: AppContainer,
    principal: Principal,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Assemble the full dashboard snapshot from every section fetcher."""
    now = now or datetime.now(UTC)
    safe_limit = clamp_limit(limit)
    traces = fetch_traces(container, principal, limit=safe_limit)
    outbox = fetch_outbox(container, principal, limit=safe_limit)
    scheduler = fetch_scheduler(container, principal, now=now, limit=safe_limit)
    agenda = fetch_agenda(container, principal, now=now, limit=safe_limit)
    reminders = fetch_reminders(container, principal, now=now, limit=safe_limit)
    events = fetch_events(container, principal, limit=safe_limit)
    states = fetch_states(container, principal, limit=safe_limit)
    memory = fetch_memory(container, principal, limit=safe_limit)
    approvals = fetch_approvals(container, principal, limit=safe_limit)
    errors = fetch_errors(container, principal, limit=safe_limit)
    context = fetch_context(container, principal)
    metrics = fetch_guardrail_metrics(container, principal)
    health = fetch_health(
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
        context=context,
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
        "context": context,
        "metrics": metrics,
    }


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
        """Return the full dashboard snapshot for one tenant principal."""
        return fetch_snapshot(self.container, principal, now=now, limit=limit)

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
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Aggregate the health summary from already-fetched sections."""
        return fetch_health(
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
            context=context,
        )

    def traces(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """Summarize tenant trace events with error grouping."""
        return fetch_traces(self.container, principal, limit=limit)

    def outbox(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """Summarize tenant outbox messages with dispatch-status counts."""
        return fetch_outbox(self.container, principal, limit=limit)

    def delivery_counts(self, principal: Principal) -> dict[str, int]:
        """Return only the closed delivery-state metric set, never row metadata."""
        return fetch_delivery_counts(self.container, principal)

    def scheduler(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Summarize scheduled reminder jobs with due/sent/pending counts."""
        return fetch_scheduler(self.container, principal, now=now, limit=limit)

    def agenda(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Summarize calendar events into upcoming/today/past buckets."""
        return fetch_agenda(self.container, principal, now=now, limit=limit)

    def reminders(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Summarize reminder jobs enriched with calendar event context."""
        return fetch_reminders(self.container, principal, now=now, limit=limit)

    def events(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """List tenant event-store rows with per-type counts."""
        return fetch_events(self.container, principal, limit=limit)

    def states(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """List durable-lite workflow states with per-status counts."""
        return fetch_states(self.container, principal, limit=limit)

    def memory(self, principal: Principal, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """List tenant memory records with confirmation and kind counts."""
        return fetch_memory(self.container, principal, limit=limit)

    def approvals(
        self,
        principal: Principal,
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Summarize pending approvals, waiting workflow states, and traces."""
        return fetch_approvals(self.container, principal, limit=limit)

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
        """Normalize trace, workflow, and outbox failures with active filters."""
        return fetch_errors(
            self.container,
            principal,
            category=category,
            run_id=run_id,
            event_type=event_type,
            source=source,
            limit=limit,
        )

    def context(self, principal: Principal) -> dict[str, Any]:
        """Aggregate LLM context-utilization stats from persisted traces."""
        return fetch_context(self.container, principal)

    def guardrail_metrics(self, principal: Principal) -> dict[str, Any]:
        """Aggregate guardrail hit-rate metrics from persisted traces."""
        return fetch_guardrail_metrics(self.container, principal)

    def render_html(
        self,
        principal: Principal,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        """Render the full HTML dashboard for one tenant principal."""
        return render_dashboard_html(self.snapshot(principal, now=now, limit=limit))
