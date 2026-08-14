"""FastAPI application factory and lifecycle wiring."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from personal_assistant import __version__
from personal_assistant.adapters.inbound.auth import LocalPrincipalProvider
from personal_assistant.infrastructure.admin import AdminDashboard
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_container import (
    build_runtime_container,
)
from personal_assistant.infrastructure.http_errors import (
    register_exception_handlers,
)
from personal_assistant.infrastructure.http_routes_admin_data import (
    register_admin_data_routes,
)
from personal_assistant.infrastructure.http_routes_admin_metrics import (
    register_admin_metric_routes,
)
from personal_assistant.infrastructure.http_routes_health import (
    register_health_routes,
)
from personal_assistant.infrastructure.http_routes_outbox import (
    register_outbox_routes,
)
from personal_assistant.infrastructure.http_routes_runtime import (
    register_runtime_routes,
)
from personal_assistant.infrastructure.http_routes_telegram import (
    register_telegram_routes,
)
from personal_assistant.infrastructure.http_routes_whatsapp import (
    register_whatsapp_routes,
)
from personal_assistant.infrastructure.http_worker import (
    _run_reminder_worker_loop,
    _utcnow,
)
from personal_assistant.infrastructure.operational import (
    PostgresWorkerHeartbeatStore,
    WorkerHeartbeatStore,
)


def create_app(
    container: AppContainer | None = None,
    settings: AppSettings | None = None,
    *,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    runtime_settings = settings or AppSettings.from_env()
    if (
        runtime_settings.reminder_worker_enabled
        and runtime_settings.persistence_backend != "postgres"
    ):
        raise RuntimeError("durable reminder delivery requires PostgreSQL")
    if (
        runtime_settings.reminder_worker_enabled
        and not runtime_settings.telegram_bot_token
    ):
        raise RuntimeError("durable reminder delivery requires Telegram configuration")
    runtime_container = container or build_runtime_container(runtime_settings)
    runtime_heartbeat_store = heartbeat_store
    if runtime_settings.reminder_worker_enabled and runtime_heartbeat_store is None:
        if not runtime_settings.database_url:
            raise RuntimeError("durable reminder delivery requires DATABASE_URL")
        runtime_heartbeat_store = PostgresWorkerHeartbeatStore(
            dsn=runtime_settings.database_url,
            schema=runtime_settings.database_schema,
        )
    local_principal_provider = (
        LocalPrincipalProvider.from_settings(runtime_settings)
        if runtime_settings.admin_token is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime_settings.reminder_worker_enabled:
            thread = threading.Thread(
                target=_run_reminder_worker_loop,
                kwargs={
                    "container": runtime_container,
                    "settings": runtime_settings,
                    "stop_event": app.state.reminder_worker_stop,
                    "heartbeat_store": runtime_heartbeat_store,
                    "clock": clock,
                },
                name="personal-assistant-reminder-worker",
                daemon=True,
            )
            app.state.reminder_worker_thread = thread
            thread.start()
        try:
            yield
        finally:
            app.state.reminder_worker_stop.set()
            thread = app.state.reminder_worker_thread
            if thread is not None:
                thread.join(timeout=5)

    app = FastAPI(
        title="Personal Assistant Runtime", version=__version__, lifespan=lifespan
    )
    app.state.container = runtime_container
    app.state.settings = runtime_settings
    app.state.reminder_worker_stop = threading.Event()
    app.state.reminder_worker_thread = None
    app.state.local_principal_provider = local_principal_provider
    dashboard = AdminDashboard(runtime_container)

    register_exception_handlers(app)
    register_health_routes(
        app,
        settings=runtime_settings,
        heartbeat_store=runtime_heartbeat_store,
        clock=clock,
    )
    register_telegram_routes(
        app,
        container=runtime_container,
        settings=runtime_settings,
    )
    register_whatsapp_routes(
        app,
        container=runtime_container,
        settings=runtime_settings,
    )
    register_admin_metric_routes(
        app,
        dashboard=dashboard,
        settings=runtime_settings,
        heartbeat_store=runtime_heartbeat_store,
        clock=clock,
    )
    register_admin_data_routes(
        app,
        dashboard=dashboard,
    )
    register_runtime_routes(
        app,
        container=runtime_container,
    )
    register_outbox_routes(
        app,
        container=runtime_container,
    )

    return app
