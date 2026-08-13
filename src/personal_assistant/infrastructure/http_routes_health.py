"""Health and readiness endpoints for the HTTP runtime."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, Response

from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_models import (
    HealthResponse,
    ReadinessResponse,
)
from personal_assistant.infrastructure.http_worker import (
    _readiness_snapshot,
    _utcnow,
)
from personal_assistant.infrastructure.operational import WorkerHeartbeatStore


def register_health_routes(
    app: FastAPI,
    settings: AppSettings,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> None:
    """Register liveness, healthz, and readiness check routes."""

    @app.get("/livez", response_model=HealthResponse, tags=["runtime"])
    def livez() -> HealthResponse:
        return HealthResponse(status="ok", service="personal_assistant")

    @app.get("/healthz", response_model=HealthResponse, tags=["runtime"])
    def healthz(response: Response) -> HealthResponse:
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = '</livez>; rel="successor-version"'
        return livez()

    @app.get("/readyz", response_model=ReadinessResponse, tags=["runtime"])
    def readyz(response: Response) -> ReadinessResponse:
        snapshot = _readiness_snapshot(
            settings=settings,
            heartbeat_store=heartbeat_store,
            clock=clock,
        )
        if snapshot.status != "ready":
            response.status_code = 503
        return snapshot
