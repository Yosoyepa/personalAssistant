"""Admin dashboard section data endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query

from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin import (
    AdminDashboard,
    clamp_limit,
)
from personal_assistant.infrastructure.http_auth import current_principal


def register_admin_data_routes(
    app: FastAPI,
    dashboard: AdminDashboard,
) -> None:
    """Register section-specific read-only admin data endpoints."""

    @app.get("/admin/approvals", tags=["admin"])
    def admin_approvals(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.approvals(principal, limit=clamp_limit(limit))

    @app.get("/admin/traces", tags=["admin"])
    def admin_traces(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.traces(principal, limit=clamp_limit(limit))

    @app.get("/admin/outbox", tags=["admin"])
    def admin_outbox(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.outbox(principal, limit=clamp_limit(limit))

    @app.get("/admin/scheduler", tags=["admin"])
    def admin_scheduler(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.scheduler(principal, limit=clamp_limit(limit))

    @app.get("/admin/agenda", tags=["admin"])
    def admin_agenda(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.agenda(principal, limit=clamp_limit(limit))

    @app.get("/admin/reminders", tags=["admin"])
    def admin_reminders(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.reminders(principal, limit=clamp_limit(limit))

    @app.get("/admin/errors", tags=["admin"])
    def admin_errors(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        category: Annotated[str | None, Query(min_length=1)] = None,
        run_id: Annotated[str | None, Query(min_length=1)] = None,
        event_type: Annotated[str | None, Query(min_length=1)] = None,
        source: Annotated[str | None, Query(min_length=1)] = None,
    ) -> dict[str, Any]:
        return dashboard.errors(
            principal,
            category=category,
            run_id=run_id,
            event_type=event_type,
            source=source,
            limit=clamp_limit(limit),
        )

    @app.get("/admin/events", tags=["admin"])
    def admin_events(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.events(principal, limit=clamp_limit(limit))

    @app.get("/admin/states", tags=["admin"])
    def admin_states(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.states(principal, limit=clamp_limit(limit))

    @app.get("/admin/memory", tags=["admin"])
    def admin_memory(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.memory(principal, limit=clamp_limit(limit))
