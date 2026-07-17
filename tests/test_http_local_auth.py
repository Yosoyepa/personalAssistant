"""End-to-end trust-boundary tests for local FastAPI surfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer, build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import create_app


ADMIN_TOKEN = "test_local_admin_token"
TENANT_ID = "configured-tenant"
PRINCIPAL_ID = "configured-local-user"
AUTHORIZATION = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


class _RecordingWorkflow:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.principals: list[Principal] = []

    def run(self, principal: Principal, request: Any) -> Any:
        self.principals.append(principal)
        return self._delegate.run(principal, request)


def _settings(*, admin_token: str | None = ADMIN_TOKEN) -> AppSettings:
    return AppSettings(
        tenant_id=TENANT_ID,
        admin_token=admin_token,
        local_auth_principal_id=PRINCIPAL_ID,
        local_auth_permission_tier=PermissionTier.P5,
        reminder_worker_enabled=False,
    )


def _client(
    container: AppContainer,
    *,
    settings: AppSettings | None = None,
    peer_host: str = "127.0.0.1",
) -> TestClient:
    return TestClient(
        create_app(container, settings=settings or _settings()),
        client=(peer_host, 50000),
    )


def _payload() -> dict[str, object]:
    return {
        "message_id": "local-auth-message-1",
        "source_event_id": "local-auth-event-1",
        "conversation_id": "local-auth-chat-1",
        "text": "recuérdame mañana a las 17 cerrar caja",
        "channel": "telegram",
        "recipient": "local-auth-chat-1",
        "now": "2026-06-20T12:00:00+00:00",
        "timezone": "America/Bogota",
    }


def _configured_principal() -> Principal:
    return Principal.for_test(
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        permission_tier=PermissionTier.P5,
    )


def _assert_no_runtime_effects(container: AppContainer) -> None:
    principal = _configured_principal()
    assert container.approvals.list_for_tenant(principal) == []
    assert container.calendar.list_events(principal) == []
    assert container.scheduler.list_for_tenant(principal) == []
    assert container.event_store.list_for_tenant(principal) == []
    assert container.outbox.list_for_tenant(principal) == []
    assert container.states.list_for_tenant(principal) == []
    assert container.traces.list_for_tenant(principal) == []


def _assert_public_rejection(
    response: Response, *, status_code: int, error_code: str
) -> None:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["error"]["code"] == error_code
    assert body["error"]["message"] in {
        "authentication required",
        "permission denied",
    }
    assert ADMIN_TOKEN not in response.text
    assert "wrong-local-token" not in response.text


INVALID_AUTHORIZATION: list[
    tuple[str, Sequence[tuple[str, str]] | Mapping[str, str]]
] = [
    ("missing", {}),
    ("wrong", {"Authorization": "Bearer wrong-local-token"}),
    ("malformed-scheme", {"Authorization": f"Basic {ADMIN_TOKEN}"}),
    ("malformed-bearer", {"Authorization": f"Bearer {ADMIN_TOKEN} extra"}),
    (
        "duplicate-bearer",
        [
            ("Authorization", f"Bearer {ADMIN_TOKEN}"),
            ("Authorization", f"Bearer {ADMIN_TOKEN}"),
        ],
    ),
]


@pytest.mark.parametrize(("case", "headers"), INVALID_AUTHORIZATION)
@pytest.mark.parametrize("surface", ["runtime", "admin"])
def test_local_surfaces_reject_invalid_bearer_credentials_without_effects(
    case: str,
    headers: Sequence[tuple[str, str]] | Mapping[str, str],
    surface: str,
) -> None:
    container = build_container()
    client = _client(container)

    if surface == "runtime":
        response = client.post(
            "/v1/runtime/reminders",
            headers=headers,
            json=_payload(),
        )
    else:
        response = client.get("/admin/health", headers=headers)

    _assert_public_rejection(
        response,
        status_code=401,
        error_code="authentication_required",
    )
    _assert_no_runtime_effects(container)


def test_every_local_route_rejects_missing_bearer() -> None:
    container = build_container()
    client = _client(container)
    local_routes = [
        route
        for route in client.app.routes
        if route.path == "/admin"
        or route.path.startswith("/admin/")
        or route.path.startswith("/v1/runtime/")
    ]

    assert local_routes
    for route in local_routes:
        methods = getattr(route, "methods", set())
        method = "GET" if "GET" in methods else "POST"
        path = route.path.replace("{approval_id}", "missing")
        kwargs: dict[str, object] = {}
        if method == "POST":
            kwargs["json"] = _payload() if path.endswith("/reminders") else {}
        response = client.request(method, path, **kwargs)

        _assert_public_rejection(
            response,
            status_code=401,
            error_code="authentication_required",
        )

    _assert_no_runtime_effects(container)


def test_remote_peer_cannot_become_local_with_forwarding_headers() -> None:
    container = build_container()
    client = _client(container, peer_host="203.0.113.8")
    spoofed_headers = {
        **AUTHORIZATION,
        "Forwarded": "for=127.0.0.1;host=localhost",
        "X-Forwarded-For": "127.0.0.1",
        "X-Forwarded-Host": "localhost",
    }

    runtime = client.post(
        "/v1/runtime/reminders",
        headers=spoofed_headers,
        json=_payload(),
    )
    admin = client.get("/admin/health", headers=spoofed_headers)

    for response in (runtime, admin):
        _assert_public_rejection(
            response,
            status_code=403,
            error_code="permission_denied",
        )
    _assert_no_runtime_effects(container)


def test_forwarding_headers_cannot_override_a_direct_loopback_peer() -> None:
    container = build_container()
    client = _client(container)
    headers = {
        **AUTHORIZATION,
        "Forwarded": "for=203.0.113.8;host=attacker.example",
        "X-Forwarded-For": "203.0.113.8",
        "X-Forwarded-Host": "attacker.example",
    }

    runtime = client.get("/v1/runtime/approvals", headers=headers)
    admin = client.get("/admin/snapshot", headers=headers)

    assert runtime.status_code == 200, runtime.text
    assert runtime.json() == []
    assert admin.status_code == 200, admin.text
    assert admin.json()["meta"]["tenant_id"] == TENANT_ID


def test_legacy_headers_and_impersonation_query_never_change_authority() -> None:
    container = build_container()
    recording_workflow = _RecordingWorkflow(container.reminder_workflow)
    container.reminder_workflow = recording_workflow  # type: ignore[assignment]
    client = _client(container)
    forged_headers = {
        **AUTHORIZATION,
        "X-Admin-Token": "wrong-local-token",
        "X-Principal-Id": "attacker",
        "X-Tenant-Id": "victim-tenant",
        "X-Permission-Tier": "P6",
        "X-Scopes": "* tenant:all",
    }
    impersonation_query = (
        "tenant_id=victim-tenant&principal_id=attacker"
        "&permission_tier=P6&scopes=tenant%3Aall"
    )

    runtime = client.post(
        f"/v1/runtime/reminders?{impersonation_query}",
        headers=forged_headers,
        json=_payload(),
    )
    admin = client.get(
        f"/admin/snapshot?{impersonation_query}",
        headers=forged_headers,
    )

    assert runtime.status_code == 202, runtime.text
    assert runtime.json()["tenant_id"] == TENANT_ID
    [principal] = recording_workflow.principals
    assert principal.tenant_id == TENANT_ID
    assert principal.principal_id == PRINCIPAL_ID
    assert principal.permission_tier == PermissionTier.P5
    assert principal.scopes == frozenset()
    assert principal.auth_provider == "local-bearer"
    assert principal.is_trusted
    assert admin.status_code == 200, admin.text
    assert admin.json()["meta"]["tenant_id"] == TENANT_ID

    approval_id = runtime.json()["approval"]["approval_id"]
    pending = container.approvals.get(_configured_principal(), approval_id)
    assert pending is not None
    assert pending.tenant_id == TENANT_ID
    assert pending.principal_id == PRINCIPAL_ID
    victim = Principal.for_test(
        principal_id="attacker",
        tenant_id="victim-tenant",
        permission_tier=PermissionTier.P6,
    )
    assert container.approvals.list_for_tenant(victim) == []


def test_x_admin_token_is_never_an_authentication_credential() -> None:
    container = build_container()
    client = _client(container)

    runtime = client.get(
        "/v1/runtime/approvals",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    admin = client.get(
        "/admin/health",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )

    for response in (runtime, admin):
        _assert_public_rejection(
            response,
            status_code=401,
            error_code="authentication_required",
        )
    _assert_no_runtime_effects(container)


def test_missing_admin_token_closes_local_surfaces_but_not_health_or_webhook() -> None:
    container = build_container()
    settings = AppSettings(
        tenant_id=TENANT_ID,
        admin_token=None,
        telegram_webhook_secret="telegram-webhook-secret",
        telegram_allowed_user_ids=frozenset({"456"}),
        reminder_worker_enabled=False,
    )
    client = _client(container, settings=settings)

    runtime = client.post(
        "/v1/runtime/reminders",
        headers=AUTHORIZATION,
        json=_payload(),
    )
    admin = client.get("/admin/health", headers=AUTHORIZATION)
    health = client.get("/healthz")
    readiness = client.get("/readyz")
    webhook = client.post(
        "/webhooks/telegram",
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "telegram-webhook-secret",
        },
        json={
            "update_id": 10,
            "message": {
                "message_id": 42,
                "chat": {"id": "chat-1"},
                "from": {"id": "456"},
                "text": "/help",
            },
        },
    )

    for response in (runtime, admin):
        _assert_public_rejection(
            response,
            status_code=401,
            error_code="authentication_required",
        )
    assert health.status_code == 200, health.text
    assert readiness.status_code == 200, readiness.text
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["command"] == "help"
    _assert_no_runtime_effects(container)
