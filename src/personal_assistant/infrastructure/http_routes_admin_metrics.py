"""Admin health and operational metric endpoints."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse

from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.admin import (
    AdminDashboard,
    clamp_limit,
)
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_auth import current_principal
from personal_assistant.infrastructure.http_models import (
    AdminGuardrailMetricsResponse,
    AdminMetricsResponse,
    DeliveryCountsResponse,
    OperationalHealthResponse,
)
from personal_assistant.infrastructure.http_worker import (
    _readiness_snapshot,
    _utcnow,
)
from personal_assistant.infrastructure.operational import (
    DELIVERY_STATUSES,
    WorkerHeartbeatStore,
    empty_delivery_counts,
)


def register_admin_metric_routes(
    app: FastAPI,
    dashboard: AdminDashboard,
    settings: AppSettings,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> None:
    """Register admin dashboard HTML, snapshot, and health metric endpoints."""

    @app.get("/admin", response_class=HTMLResponse, tags=["admin"])
    def admin_page(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> HTMLResponse:
        return HTMLResponse(dashboard.render_html(principal, limit=clamp_limit(limit)))

    @app.get("/admin/snapshot", tags=["admin"])
    def admin_snapshot(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.snapshot(principal, limit=clamp_limit(limit))

    @app.get("/admin/health", tags=["admin"])
    def admin_health(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.snapshot(principal, limit=clamp_limit(limit))["health"]

    @app.get(
        "/admin/metrics",
        response_model=AdminMetricsResponse,
        tags=["admin"],
    )
    def admin_metrics(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AdminMetricsResponse:
        counts = empty_delivery_counts()
        metrics_status: Literal["ok", "error"] = "ok"
        try:
            observed = dashboard.delivery_counts(principal)
            counts.update(
                {status: int(observed.get(status, 0)) for status in DELIVERY_STATUSES}
            )
        except Exception:
            metrics_status = "error"
        readiness = _readiness_snapshot(
            settings=settings,
            heartbeat_store=heartbeat_store,
            clock=clock,
        )
        worker_status: Literal["disabled", "ok", "missing", "stale", "error"]
        worker_status = "disabled"
        if settings.reminder_worker_enabled:
            worker_detail = readiness.detail or ""
            if readiness.checks.get("worker_heartbeat") == "ok":
                worker_status = "ok"
            elif worker_detail.endswith("missing"):
                worker_status = "missing"
            elif worker_detail.endswith("stale"):
                worker_status = "stale"
            else:
                worker_status = "error"
        return AdminMetricsResponse(
            counts=DeliveryCountsResponse(**counts),
            health=OperationalHealthResponse(
                readiness=readiness.status,
                worker=worker_status,
                metrics=metrics_status,
            ),
        )

    @app.get(
        "/admin/guardrails/metrics",
        response_model=AdminGuardrailMetricsResponse,
        tags=["admin"],
    )
    def admin_guardrail_metrics(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AdminGuardrailMetricsResponse:
        return AdminGuardrailMetricsResponse(**dashboard.guardrail_metrics(principal))
