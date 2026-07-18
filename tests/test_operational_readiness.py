from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import personal_assistant
from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
)
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage, OutboxStatus
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import (
    _readiness_snapshot,
    _run_reminder_worker_loop,
    create_app,
)
from personal_assistant.infrastructure.migrations import MigrationStatus
from personal_assistant.infrastructure.operational import assess_heartbeat


NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
AUTH = {"Authorization": "Bearer alpha-admin-token"}
PII_MARKERS = (
    "private-recipient@example.test",
    "+573001112233",
    "msg-private-id",
    "tenant-private-id",
    "body-private-marker",
)


@dataclass
class FakeHeartbeatStore:
    observed_at: datetime | None

    def record(self, observed_at: datetime) -> None:
        self.observed_at = observed_at

    def latest(self) -> datetime | None:
        return self.observed_at


def _ready_migrations(*args: object, **kwargs: object) -> MigrationStatus:
    return MigrationStatus(
        schema="assistant_test", history_exists=True, applied=(), pending=()
    )


def test_livez_has_no_dependencies_and_healthz_is_deprecated_alias() -> None:
    client = TestClient(create_app(container=build_container(), settings=AppSettings()))

    live = client.get("/livez")
    legacy = client.get("/healthz")

    assert live.status_code == legacy.status_code == 200
    assert live.json() == legacy.json() == {
        "status": "ok",
        "service": "personal_assistant",
    }
    assert legacy.headers["deprecation"] == "true"
    assert legacy.headers["link"] == '</livez>; rel="successor-version"'


def test_runtime_and_package_versions_share_the_project_source() -> None:
    app = create_app(container=build_container(), settings=AppSettings())
    with open("pyproject.toml", "rb") as project_file:
        project_version = tomllib.load(project_file)["project"]["version"]

    assert app.version == personal_assistant.__version__ == project_version


def test_fake_clock_rejects_missing_stale_and_future_heartbeats() -> None:
    assert assess_heartbeat(None, now=NOW, timeout_seconds=30).status == "missing"
    assert not assess_heartbeat(
        NOW - timedelta(seconds=31), now=NOW, timeout_seconds=30
    ).fresh
    assert assess_heartbeat(
        NOW - timedelta(seconds=30), now=NOW, timeout_seconds=30
    ).fresh
    future = assess_heartbeat(
        NOW + timedelta(microseconds=1), now=NOW, timeout_seconds=30
    )
    assert future.status == "stale"
    assert not future.fresh


def test_enabled_worker_rejects_timeout_not_greater_than_interval() -> None:
    with pytest.raises(ValueError, match="HEARTBEAT_TIMEOUT_SECONDS"):
        AppSettings(
            reminder_worker_enabled=True,
            reminder_worker_interval_seconds=15,
            reminder_worker_heartbeat_timeout_seconds=15,
        )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "field_name",
    [
        "reminder_worker_interval_seconds",
        "reminder_worker_heartbeat_timeout_seconds",
    ],
)
def test_worker_timing_rejects_non_finite_values(
    field_name: str, invalid: float
) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        AppSettings(reminder_worker_enabled=True, **{field_name: invalid})


@pytest.mark.parametrize("invalid", ["nan", "inf", "-inf"])
@pytest.mark.parametrize(
    "environment_name",
    [
        "REMINDER_WORKER_INTERVAL_SECONDS",
        "REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS",
    ],
)
def test_worker_timing_environment_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch, environment_name: str, invalid: str
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("REMINDER_WORKER_ENABLED", "true")
    monkeypatch.setenv(environment_name, invalid)

    with pytest.raises(ValueError, match="must be finite"):
        AppSettings.from_env()


@pytest.mark.parametrize("invalid", [0.0, -1.0])
@pytest.mark.parametrize(
    "field_name",
    [
        "reminder_worker_interval_seconds",
        "reminder_worker_heartbeat_timeout_seconds",
    ],
)
def test_worker_timing_rejects_non_positive_values(
    field_name: str, invalid: float
) -> None:
    with pytest.raises(ValueError, match="must be greater than zero"):
        AppSettings(reminder_worker_enabled=True, **{field_name: invalid})


@pytest.mark.parametrize("invalid", ["0", "-1"])
@pytest.mark.parametrize(
    "environment_name",
    [
        "REMINDER_WORKER_INTERVAL_SECONDS",
        "REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS",
    ],
)
def test_worker_timing_environment_rejects_non_positive_values(
    monkeypatch: pytest.MonkeyPatch, environment_name: str, invalid: str
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("REMINDER_WORKER_ENABLED", "true")
    monkeypatch.setenv(environment_name, invalid)

    with pytest.raises(ValueError, match="must be greater than zero"):
        AppSettings.from_env()


def test_readiness_requires_fresh_worker_heartbeat(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "personal_assistant.infrastructure.http.migration_status", _ready_migrations
    )
    settings = AppSettings(
        persistence_backend="postgres",
        database_url="postgresql://not-opened",
        database_schema="assistant_test",
        telegram_bot_token="not-called",
        reminder_worker_enabled=True,
        reminder_worker_heartbeat_timeout_seconds=30,
    )

    missing = _readiness_snapshot(
        settings=settings,
        heartbeat_store=FakeHeartbeatStore(None),
        clock=lambda: NOW,
    )
    stale = _readiness_snapshot(
        settings=settings,
        heartbeat_store=FakeHeartbeatStore(NOW - timedelta(seconds=31)),
        clock=lambda: NOW,
    )
    fresh = _readiness_snapshot(
        settings=settings,
        heartbeat_store=FakeHeartbeatStore(NOW - timedelta(seconds=3)),
        clock=lambda: NOW,
    )

    assert missing.status == stale.status == "not_ready"
    assert missing.checks["worker_heartbeat"] == "error"
    assert missing.detail == "worker heartbeat is missing"
    assert stale.detail == "worker heartbeat is stale"
    assert fresh.status == "ready"
    assert fresh.checks == {
        "process": "ok",
        "database": "ok",
        "migrations": "ok",
        "worker_heartbeat": "ok",
    }


def test_successful_worker_tick_records_heartbeat_after_completion() -> None:
    class SuccessfulWorker:
        def __init__(self) -> None:
            self.completed = False

        def run_once(self, principal: Principal) -> None:
            self.completed = True

    class StopAfterOneTick:
        def __init__(self) -> None:
            self.waits = 0

        def is_set(self) -> bool:
            return self.waits >= 1

        def wait(self, timeout: float) -> bool:
            self.waits += 1
            return True

    class RecordingHeartbeat(FakeHeartbeatStore):
        def record(self, observed_at: datetime) -> None:
            assert worker.completed
            super().record(observed_at)

    worker = SuccessfulWorker()
    heartbeat = RecordingHeartbeat(None)
    settings = AppSettings(
        tenant_id="tenant-a",
        persistence_backend="postgres",
        database_url="postgresql://not-opened",
        telegram_bot_token="not-called",
        reminder_worker_enabled=True,
        reminder_worker_interval_seconds=1,
    )

    _run_reminder_worker_loop(
        container=SimpleNamespace(reminder_worker=worker, traces=SimpleNamespace()),
        settings=settings,
        stop_event=StopAfterOneTick(),
        heartbeat_store=heartbeat,
        clock=lambda: NOW,
    )

    assert heartbeat.latest() == NOW


def test_admin_metrics_are_protected_closed_and_pii_free() -> None:
    container = build_container()
    actor = Principal.for_test(
        principal_id="private-principal-id",
        tenant_id="tenant-private-id",
        permission_tier=PermissionTier.P5,
    )
    event = CloudEvent(
        id="msg-private-id",
        type="notification.requested",
        source="test",
        subject="private-recipient@example.test",
        tenant_id=actor.tenant_id,
        data={
            "recipient": "+573001112233",
            "body": "body-private-marker",
        },
        time=NOW,
    )
    error = DeliveryError(
        category=DeliveryErrorCategory.network,
        code=DeliveryErrorCode.provider_unavailable,
        occurred_at=NOW,
    )
    messages = [
        OutboxMessage(
            id=f"msg-private-id-{status.value}",
            tenant_id=actor.tenant_id,
            event=event.model_copy(update={"id": f"event-{status.value}"}),
            idempotency_key=f"private-{status.value}",
            dispatch_status=status,
            claim_token=(
                "private-claim-token"
                if status in {OutboxStatus.claimed, OutboxStatus.sending}
                else None
            ),
            claim_owner=(
                "private-claim-owner"
                if status in {OutboxStatus.claimed, OutboxStatus.sending}
                else None
            ),
            claimed_until=(
                NOW + timedelta(minutes=1)
                if status in {OutboxStatus.claimed, OutboxStatus.sending}
                else None
            ),
            attempts=(
                1
                if status
                in {OutboxStatus.sending, OutboxStatus.failed, OutboxStatus.uncertain}
                else 0
            ),
            sending_at=(
                NOW
                if status
                in {OutboxStatus.sending, OutboxStatus.failed, OutboxStatus.uncertain}
                else None
            ),
            published_at=NOW if status == OutboxStatus.published else None,
            last_error=(
                error
                if status in {OutboxStatus.failed, OutboxStatus.uncertain}
                else None
            ),
            created_at=NOW,
        )
        for status in OutboxStatus
    ]

    class MetricsOutbox:
        def list_for_tenant(self, principal: Principal) -> list[OutboxMessage]:
            return [
                message
                for message in messages
                if message.tenant_id == principal.tenant_id
            ]

    container.outbox = MetricsOutbox()
    settings = AppSettings(
        tenant_id=actor.tenant_id,
        admin_token="alpha-admin-token",
        local_auth_principal_id="metrics-admin",
        local_auth_permission_tier=PermissionTier.P5,
    )
    client = TestClient(
        create_app(container=container, settings=settings),
        client=("127.0.0.1", 50000),
    )
    remote = TestClient(
        create_app(container=container, settings=settings),
        client=("203.0.113.10", 50000),
    )

    assert client.get("/admin/metrics").status_code == 401
    assert client.get(
        "/admin/metrics", headers={"Authorization": "Bearer wrong-token"}
    ).status_code == 401
    assert remote.get("/admin/metrics", headers=AUTH).status_code == 403
    response = client.get("/admin/metrics", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "counts": {
            "pending": 1,
            "claimed": 1,
            "sending": 1,
            "published": 1,
            "failed": 1,
            "uncertain": 1,
        },
        "health": {
            "liveness": "ok",
            "readiness": "ready",
            "worker": "disabled",
            "metrics": "ok",
        },
    }
    serialized = json.dumps(response.json(), sort_keys=True)
    assert all(marker not in serialized for marker in PII_MARKERS)


def test_admin_metrics_store_failure_is_closed_and_pii_free() -> None:
    class FailingOutbox:
        def list_for_tenant(self, principal: Principal) -> list[OutboxMessage]:
            raise RuntimeError(
                "body-private-marker recipient=private-recipient@example.test "
                "message_id=msg-private-id"
            )

    container = build_container()
    container.outbox = FailingOutbox()
    settings = AppSettings(
        tenant_id="tenant-private-id",
        admin_token="alpha-admin-token",
        local_auth_principal_id="metrics-admin",
        local_auth_permission_tier=PermissionTier.P5,
    )
    client = TestClient(
        create_app(container=container, settings=settings),
        client=("127.0.0.1", 50000),
    )

    response = client.get("/admin/metrics", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "counts": {
            "pending": 0,
            "claimed": 0,
            "sending": 0,
            "published": 0,
            "failed": 0,
            "uncertain": 0,
        },
        "health": {
            "liveness": "ok",
            "readiness": "ready",
            "worker": "disabled",
            "metrics": "error",
        },
    }
    serialized = json.dumps(response.json(), sort_keys=True)
    assert all(marker not in serialized for marker in PII_MARKERS)
