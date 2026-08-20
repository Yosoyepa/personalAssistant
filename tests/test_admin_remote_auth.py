"""Tests for remote admin authentication, session cookies, and startup guardrails."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from personal_assistant.adapters.inbound.auth import (
    LocalPrincipalConfig,
    LocalPrincipalProvider,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_app import create_app

VALID_STRONG_TOKEN = "test_valid_strong_token_32_chars_0123456789"
INVALID_TOKEN = "test_invalid_token_value_0123456789"
REMOTE_CLIENT = ("203.0.113.10", 50000)
LOOPBACK_CLIENT = ("127.0.0.1", 50000)


def _make_app(
    *, admin_token: str | None = VALID_STRONG_TOKEN, allow_remote: bool = False
):
    settings = AppSettings(
        admin_token=admin_token,
        admin_allow_remote=allow_remote,
        tenant_id="personal",
        local_auth_principal_id="admin-user",
        local_auth_permission_tier=PermissionTier.P5,
    )
    container = build_container()
    return create_app(container=container, settings=settings)


# ---------------------------------------------------------------------------
# Unit tests for LocalPrincipalProvider with allow_remote and cookies
# ---------------------------------------------------------------------------


def test_provider_allow_remote_property() -> None:
    config_off = LocalPrincipalConfig(
        token=VALID_STRONG_TOKEN,
        tenant_id="personal",
        principal_id="admin-user",
        permission_tier=PermissionTier.P5,
        allow_remote=False,
    )
    provider_off = LocalPrincipalProvider(config_off)
    assert not provider_off.allow_remote

    config_on = LocalPrincipalConfig(
        token=VALID_STRONG_TOKEN,
        tenant_id="personal",
        principal_id="admin-user",
        permission_tier=PermissionTier.P5,
        allow_remote=True,
    )
    provider_on = LocalPrincipalProvider(config_on)
    assert provider_on.allow_remote


def test_provider_rejects_remote_peer_when_allow_remote_disabled() -> None:
    provider = LocalPrincipalProvider(
        LocalPrincipalConfig(
            token=VALID_STRONG_TOKEN,
            tenant_id="personal",
            principal_id="admin-user",
            permission_tier=PermissionTier.P5,
            allow_remote=False,
        )
    )
    with pytest.raises(AssistantError) as exc_info:
        provider.authenticate(
            peer_host="203.0.113.10",
            headers={"Authorization": f"Bearer {VALID_STRONG_TOKEN}"},
        )
    assert exc_info.value.code is ErrorCode.PERMISSION_DENIED


def test_provider_accepts_remote_peer_when_allow_remote_enabled() -> None:
    provider = LocalPrincipalProvider(
        LocalPrincipalConfig(
            token=VALID_STRONG_TOKEN,
            tenant_id="personal",
            principal_id="admin-user",
            permission_tier=PermissionTier.P5,
            allow_remote=True,
        )
    )
    principal = provider.authenticate(
        peer_host="203.0.113.10",
        headers={"Authorization": f"Bearer {VALID_STRONG_TOKEN}"},
    )
    assert principal.is_trusted
    assert principal.principal_id == "admin-user"
    assert principal.tenant_id == "personal"


def test_provider_accepts_valid_cookie_token() -> None:
    provider = LocalPrincipalProvider(
        LocalPrincipalConfig(
            token=VALID_STRONG_TOKEN,
            tenant_id="personal",
            principal_id="admin-user",
            permission_tier=PermissionTier.P5,
            allow_remote=True,
        )
    )
    principal = provider.authenticate(
        peer_host="203.0.113.10",
        headers={},
        cookies={"admin_token": VALID_STRONG_TOKEN},
    )
    assert principal.is_trusted
    assert principal.principal_id == "admin-user"


def test_provider_rejects_invalid_cookie_token() -> None:
    provider = LocalPrincipalProvider(
        LocalPrincipalConfig(
            token=VALID_STRONG_TOKEN,
            tenant_id="personal",
            principal_id="admin-user",
            permission_tier=PermissionTier.P5,
            allow_remote=True,
        )
    )
    with pytest.raises(AssistantError) as exc_info:
        provider.authenticate(
            peer_host="203.0.113.10",
            headers={},
            cookies={"admin_token": "wrong-token-value"},
        )
    assert exc_info.value.code is ErrorCode.AUTHENTICATION_REQUIRED


# ---------------------------------------------------------------------------
# Gherkin Scenario Outline 1: Access depends on peer, token and remote opt-in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote_flag", "client_tuple", "auth_header", "expected_status"),
    [
        (False, LOOPBACK_CLIENT, f"Bearer {VALID_STRONG_TOKEN}", 200),
        (False, LOOPBACK_CLIENT, None, 401),
        (False, REMOTE_CLIENT, f"Bearer {VALID_STRONG_TOKEN}", 403),
        (True, LOOPBACK_CLIENT, f"Bearer {VALID_STRONG_TOKEN}", 200),
        (True, REMOTE_CLIENT, f"Bearer {VALID_STRONG_TOKEN}", 200),
        (True, REMOTE_CLIENT, None, 401),
        (True, REMOTE_CLIENT, f"Bearer {INVALID_TOKEN}", 401),
    ],
)
def test_scenario_access_depends_on_peer_token_and_remote_optin(
    remote_flag: bool,
    client_tuple: tuple[str, int],
    auth_header: str | None,
    expected_status: int,
) -> None:
    app = _make_app(allow_remote=remote_flag)
    client = TestClient(app, client=client_tuple)
    headers = {"Authorization": auth_header} if auth_header else {}
    response = client.get("/admin/approvals", headers=headers)
    assert response.status_code == expected_status


# ---------------------------------------------------------------------------
# Gherkin Scenario Outline 2: Browser login issues a hardened session cookie
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token_kind", "token_val", "expected_status", "cookie_expected"),
    [
        ("valid", VALID_STRONG_TOKEN, 200, True),
        ("invalid", INVALID_TOKEN, 401, False),
    ],
)
def test_scenario_browser_login_issues_hardened_session_cookie(
    token_kind: str,
    token_val: str,
    expected_status: int,
    cookie_expected: bool,
) -> None:
    app = _make_app(allow_remote=True)
    client = TestClient(app, client=REMOTE_CLIENT)
    response = client.post("/admin/login", data={"token": token_val})
    assert response.status_code == expected_status

    if cookie_expected:
        cookie_header = response.headers.get("set-cookie", "")
        assert "admin_token=" in cookie_header
        assert "HttpOnly" in cookie_header or "httponly" in cookie_header
        assert "Secure" in cookie_header or "secure" in cookie_header
        assert "SameSite=strict" in cookie_header or "samesite=strict" in cookie_header
        assert "Path=/admin" in cookie_header or "path=/admin" in cookie_header
        assert "Max-Age=43200" in cookie_header or "max-age=43200" in cookie_header
    else:
        assert "admin_token" not in response.cookies


def test_login_rejected_when_remote_disabled_on_remote_peer() -> None:
    app = _make_app(allow_remote=False)
    client = TestClient(app, client=REMOTE_CLIENT)
    response = client.post("/admin/login", data={"token": VALID_STRONG_TOKEN})
    assert response.status_code == 403
    assert "admin_token" not in response.cookies


def test_login_rejected_without_configured_provider() -> None:
    settings = AppSettings(
        admin_token=None,
        admin_allow_remote=False,
    )
    container = build_container()
    app = create_app(container=container, settings=settings)
    client = TestClient(app, client=LOOPBACK_CLIENT)
    response = client.post("/admin/login", data={"token": "some-token"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Gherkin Scenario 3 & 4: Session cookie authentication and logout revocation
# ---------------------------------------------------------------------------


def test_scenario_valid_session_cookie_authenticates_and_logout_revokes() -> None:
    app = _make_app(allow_remote=True)
    client = TestClient(app, base_url="https://testserver", client=REMOTE_CLIENT)

    # 1. Login
    login_resp = client.post("/admin/login", data={"token": VALID_STRONG_TOKEN})
    assert login_resp.status_code == 200
    assert "admin_token" in client.cookies

    # 2. Request with session cookie
    auth_resp = client.get("/admin/approvals")
    assert auth_resp.status_code == 200

    # 3. Logout
    logout_resp = client.post("/admin/logout")
    assert logout_resp.status_code == 200

    # 4. Request with cleared cookie returns 401
    post_logout_resp = client.get("/admin/approvals")
    assert post_logout_resp.status_code == 401


# ---------------------------------------------------------------------------
# Gherkin Scenario Outline 5: Remote mode refuses weak tokens at startup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "allow_remote", "should_succeed"),
    [
        ("short", True, False),
        ("", True, False),
        (None, True, False),
        ("a" * 31, True, False),
        ("a" * 32, True, True),
        ("random_long_token_of_at_least_32_characters_long", True, True),
        ("short", False, True),
    ],
)
def test_scenario_remote_mode_startup_token_guardrail(
    token: str | None,
    allow_remote: bool,
    should_succeed: bool,
) -> None:
    settings = AppSettings(
        admin_token=token,
        admin_allow_remote=allow_remote,
    )
    container = build_container()

    if should_succeed:
        app = create_app(container=container, settings=settings)
        assert app is not None
    else:
        with pytest.raises(
            RuntimeError,
            match="remote admin access requires ADMIN_TOKEN with at least 32 characters",
        ):
            create_app(container=container, settings=settings)


def test_verify_token_edge_cases() -> None:
    provider = LocalPrincipalProvider(
        LocalPrincipalConfig(
            token=VALID_STRONG_TOKEN,
            tenant_id="personal",
            principal_id="admin-user",
            permission_tier=PermissionTier.P5,
            allow_remote=True,
        )
    )
    assert not provider.verify_token(None)
    assert not provider.verify_token("")
    assert not provider.verify_token("invalid bearer with spaces")
    assert not provider.verify_token("token_with_unicode_ñ")
    assert not provider.verify_token(cast(Any, 123))


def test_login_with_malformed_body_and_missing_field() -> None:
    app = _make_app(allow_remote=True)
    client = TestClient(app, client=REMOTE_CLIENT)

    # Missing token field in form
    resp_empty_field = client.post("/admin/login", data={"other": "value"})
    assert resp_empty_field.status_code == 401

    # Non-UTF8 body
    resp_bad_bytes = client.post(
        "/admin/login",
        content=b"\xff\xfe\xfd",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp_bad_bytes.status_code == 401
