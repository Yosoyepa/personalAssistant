"""Background worker loop and readiness checks for the HTTP runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal

from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.admin import local_admin_principal
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_dynamic import get_http_attribute
from personal_assistant.infrastructure.http_models import ReadinessResponse
from personal_assistant.infrastructure.migrations import (
    MigrationChecksumError,
    MigrationError,
    MigrationHistoryError,
    migration_status,
)
from personal_assistant.infrastructure.operational import (
    WorkerHeartbeatStore,
    assess_heartbeat,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _run_reminder_worker_loop(
    *,
    container: AppContainer,
    settings: AppSettings,
    stop_event: threading.Event,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Run the background worker loop for periodic reminder processing."""
    if settings.persistence_backend != "postgres":
        raise RuntimeError("durable reminder delivery requires PostgreSQL")
    if not settings.telegram_bot_token:
        raise RuntimeError("durable reminder delivery requires Telegram configuration")
    principal = local_admin_principal(
        tenant_id=settings.tenant_id,
        principal_id="reminder-worker",
        permission_tier=PermissionTier.P5,
    )
    active_clock = clock or _utcnow
    while not stop_event.is_set():
        try:
            container.reminder_worker.run_once(principal)
            if heartbeat_store is not None:
                heartbeat_store.record(active_clock())
        except Exception:
            # A shared persistence outage must not terminate the worker loop.
            with suppress(Exception):
                container.traces.write(
                    TraceEvent(
                        run_id="reminder-worker",
                        agent_id="personal_assistant",
                        event_type=TraceEventType.agent_failed,
                        tenant_id=settings.tenant_id,
                        error={"code": "worker_tick_failed"},
                    )
                )
        stop_event.wait(settings.reminder_worker_interval_seconds)


def _readiness_snapshot(
    *,
    settings: AppSettings,
    heartbeat_store: WorkerHeartbeatStore | None,
    clock: Callable[[], datetime],
) -> ReadinessResponse:
    """Assess readiness status of process, database migrations, and worker heartbeat."""
    checks: dict[str, Literal["ok", "pending", "error"]] = {"process": "ok"}
    pending_migrations: list[str] = []
    detail: str | None = None
    migrations_ready = settings.persistence_backend != "postgres"

    if settings.persistence_backend == "postgres":
        checks["database"] = "error"
        status_fn = get_http_attribute("migration_status", migration_status)
        try:
            status = status_fn(
                dsn=settings.database_url,
                schema=settings.database_schema,
            )
        except MigrationChecksumError:
            checks["database"] = "ok"
            checks["migrations"] = "error"
            detail = "applied migration checksum mismatch"
        except MigrationHistoryError:
            checks["database"] = "ok"
            checks["migrations"] = "error"
            detail = "migration history is incompatible with this release"
        except MigrationError:
            checks["migrations"] = "error"
            detail = "migration status could not be read"
        except Exception:
            checks["migrations"] = "error"
            detail = "database unavailable or migration status could not be read"
        else:
            checks["database"] = "ok"
            pending_migrations = [migration.label for migration in status.pending]
            checks["migrations"] = "pending" if pending_migrations else "ok"
            migrations_ready = not pending_migrations
            if pending_migrations:
                detail = "database migrations are pending"

    if settings.reminder_worker_enabled:
        if not migrations_ready:
            checks["worker_heartbeat"] = "pending"
        elif heartbeat_store is None:
            checks["worker_heartbeat"] = "error"
            detail = detail or "worker heartbeat is unavailable"
        else:
            try:
                assessment = assess_heartbeat(
                    heartbeat_store.latest(),
                    now=clock(),
                    timeout_seconds=settings.reminder_worker_heartbeat_timeout_seconds,
                )
            except Exception:
                checks["worker_heartbeat"] = "error"
                detail = detail or "worker heartbeat could not be read"
            else:
                checks["worker_heartbeat"] = "ok" if assessment.fresh else "error"
                if not assessment.fresh:
                    detail = f"worker heartbeat is {assessment.status}"

    if any(check != "ok" for check in checks.values()):
        return ReadinessResponse(
            status="not_ready",
            checks=checks,
            pending_migrations=pending_migrations,
            detail=detail,
        )
    return ReadinessResponse(status="ready", checks=checks)
