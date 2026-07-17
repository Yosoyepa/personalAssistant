"""Unit and adversarial coverage for the framework-agnostic local auth boundary."""

from __future__ import annotations

import pytest

import personal_assistant.adapters.inbound.auth as auth_module
from personal_assistant.adapters.inbound.auth import (
    LocalPrincipalConfig,
    LocalPrincipalProvider,
    is_loopback_peer,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import require_trusted_principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.config import AppSettings


FAKE_TOKEN = "test_local_token_0123456789"


def _config(**overrides: object) -> LocalPrincipalConfig:
    values: dict[str, object] = {
        "token": FAKE_TOKEN,
        "tenant_id": "configured-tenant",
        "principal_id": "configured-user",
        "permission_tier": PermissionTier.P4,
    }
    values.update(overrides)
    return LocalPrincipalConfig(**values)  # type: ignore[arg-type]


def _provider(**overrides: object) -> LocalPrincipalProvider:
    return LocalPrincipalProvider(_config(**overrides))


def _authorization(token: str = FAKE_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "peer_host",
    [
        "127.0.0.1",
        "127.255.255.254",
        "::1",
        "::ffff:127.0.0.1",
    ],
)
def test_provider_accepts_numeric_ipv4_and_ipv6_loopback(peer_host: str) -> None:
    principal = _provider().authenticate(
        peer_host=peer_host,
        headers=_authorization(),
    )

    require_trusted_principal(principal)
    assert principal.is_trusted
    assert principal.principal_id == "configured-user"
    assert principal.auth_subject == "configured-user"
    assert principal.tenant_id == "configured-tenant"
    assert principal.permission_tier is PermissionTier.P4
    assert principal.auth_provider == "local-bearer"
    assert principal.permissions == frozenset()
    assert principal.scopes == frozenset()


@pytest.mark.parametrize(
    "peer_host",
    [
        None,
        "",
        " localhost",
        "localhost",
        "LOCALHOST",
        "127.0.0.1:8000",
        "[::1]",
        "192.168.1.20",
        "10.0.0.8",
        "::2",
        "::ffff:192.168.1.20",
        "example.test",
    ],
)
def test_provider_rejects_non_numeric_or_non_loopback_peer(
    peer_host: str | None,
) -> None:
    with pytest.raises(AssistantError) as captured:
        _provider().authenticate(
            peer_host=peer_host,
            headers=_authorization(),
        )

    assert captured.value.code is ErrorCode.PERMISSION_DENIED
    assert captured.value.response.tenant_id is None


def test_forwarding_headers_never_turn_remote_peer_into_loopback() -> None:
    headers = {
        **_authorization(),
        "Forwarded": "for=127.0.0.1;host=localhost",
        "X-Forwarded-For": "127.0.0.1",
        "X-Real-IP": "127.0.0.1",
    }

    with pytest.raises(AssistantError) as captured:
        _provider().authenticate(peer_host="203.0.113.8", headers=headers)

    assert captured.value.code is ErrorCode.PERMISSION_DENIED


def test_forwarding_headers_cannot_override_real_loopback_peer() -> None:
    principal = _provider().authenticate(
        peer_host="::1",
        headers={
            **_authorization(),
            "Forwarded": "for=203.0.113.8",
            "X-Forwarded-For": "203.0.113.8",
        },
    )

    assert principal.tenant_id == "configured-tenant"


def test_identity_permission_and_scope_headers_have_no_authority() -> None:
    principal = _provider().authenticate(
        peer_host="127.0.0.1",
        headers={
            **_authorization(),
            "X-Tenant-Id": "victim-tenant",
            "X-Principal-Id": "attacker",
            "X-Permission-Tier": "P6",
            "X-Scopes": "* admin write",
        },
    )

    assert principal.tenant_id == "configured-tenant"
    assert principal.principal_id == "configured-user"
    assert principal.permission_tier is PermissionTier.P4
    assert principal.scopes == frozenset()


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_bearer_scheme_is_strict_but_case_insensitive_per_http(
    scheme: str,
) -> None:
    principal = _provider().authenticate(
        peer_host="127.0.0.1",
        headers={"authorization": f"{scheme} {FAKE_TOKEN}"},
    )

    assert principal.is_trusted


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": FAKE_TOKEN},
        {"Authorization": f"Basic {FAKE_TOKEN}"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": f"Bearer  {FAKE_TOKEN}"},
        {"Authorization": f"Bearer\t{FAKE_TOKEN}"},
        {"Authorization": f" Bearer {FAKE_TOKEN}"},
        {"Authorization": f"Bearer {FAKE_TOKEN} "},
        {"Authorization": f'Bearer "{FAKE_TOKEN}"'},
        {"Authorization": f"Bearer {FAKE_TOKEN},other"},
        {"Authorization": f"Bearer {FAKE_TOKEN}\r\nX-Evil: yes"},
        {"X-Admin-Token": FAKE_TOKEN},
        {
            "Authorization": f"Bearer {FAKE_TOKEN}",
            "authorization": f"Bearer {FAKE_TOKEN}",
        },
    ],
)
def test_provider_rejects_missing_legacy_duplicate_or_malformed_credentials(
    headers: dict[str, str],
) -> None:
    with pytest.raises(AssistantError) as captured:
        _provider().authenticate(peer_host="127.0.0.1", headers=headers)

    assert captured.value.code is ErrorCode.AUTHENTICATION_REQUIRED
    assert str(captured.value) == "authentication required"
    assert FAKE_TOKEN not in str(captured.value)


def test_wrong_bearer_token_fails_without_identity_context() -> None:
    with pytest.raises(AssistantError) as captured:
        _provider().authenticate(
            peer_host="127.0.0.1",
            headers=_authorization("different-fake-token"),
        )

    assert captured.value.code is ErrorCode.AUTHENTICATION_REQUIRED
    assert captured.value.response.tenant_id is None
    assert "different-fake-token" not in str(captured.value)


def test_token_comparison_uses_constant_length_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons: list[tuple[bytes, bytes]] = []

    def capture_compare_digest(supplied: bytes, expected: bytes) -> bool:
        comparisons.append((supplied, expected))
        return False

    monkeypatch.setattr(auth_module, "compare_digest", capture_compare_digest)

    with pytest.raises(AssistantError):
        _provider().authenticate(
            peer_host="127.0.0.1",
            headers=_authorization("wrong-token-with-a-different-length"),
        )

    assert len(comparisons) == 1
    supplied, expected = comparisons[0]
    assert len(supplied) == len(expected) == 32


@pytest.mark.parametrize(
    ("overrides", "setting_name"),
    [
        ({"token": None}, "ADMIN_TOKEN"),
        ({"token": ""}, "ADMIN_TOKEN"),
        ({"token": "has whitespace"}, "ADMIN_TOKEN"),
        ({"token": "tóken"}, "ADMIN_TOKEN"),
        ({"tenant_id": ""}, "ASSISTANT_TENANT_ID"),
        ({"tenant_id": " tenant"}, "ASSISTANT_TENANT_ID"),
        ({"tenant_id": "tenant\nother"}, "ASSISTANT_TENANT_ID"),
        ({"principal_id": ""}, "LOCAL_AUTH_PRINCIPAL_ID"),
        ({"principal_id": "user\nother"}, "LOCAL_AUTH_PRINCIPAL_ID"),
        ({"principal_id": "x" * 201}, "LOCAL_AUTH_PRINCIPAL_ID"),
        ({"permission_tier": "P7"}, "LOCAL_AUTH_PERMISSION_TIER"),
    ],
)
def test_invalid_local_authority_configuration_fails_closed(
    overrides: dict[str, object],
    setting_name: str,
) -> None:
    with pytest.raises(ValueError) as captured:
        _config(**overrides)

    assert setting_name in str(captured.value)


def test_printable_unicode_identity_configuration_is_preserved() -> None:
    principal = _provider(
        tenant_id="organización-á",
        principal_id="usuaria-ñ",
    ).authenticate(peer_host="::1", headers=_authorization())

    assert principal.tenant_id == "organización-á"
    assert principal.principal_id == "usuaria-ñ"


def test_provider_builds_from_app_settings_and_does_not_retain_request_authority() -> (
    None
):
    settings = AppSettings(
        tenant_id="settings-tenant",
        admin_token=FAKE_TOKEN,
        local_auth_principal_id="settings-user",
        local_auth_permission_tier=PermissionTier.P3,
    )

    principal = LocalPrincipalProvider.from_settings(settings).authenticate(
        peer_host="127.0.0.1",
        headers={
            **_authorization(),
            "X-Tenant-Id": "request-tenant",
            "X-Principal-Id": "request-user",
            "X-Permission-Tier": "P6",
        },
    )

    assert principal.tenant_id == "settings-tenant"
    assert principal.principal_id == "settings-user"
    assert principal.permission_tier is PermissionTier.P3


def test_provider_fails_closed_when_shared_admin_token_is_not_configured() -> None:
    with pytest.raises(ValueError, match="ADMIN_TOKEN"):
        LocalPrincipalProvider.from_settings(AppSettings())


def test_app_settings_load_local_authority_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("ADMIN_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("ASSISTANT_TENANT_ID", "environment-tenant")
    monkeypatch.setenv("LOCAL_AUTH_PRINCIPAL_ID", "environment-user")
    monkeypatch.setenv("LOCAL_AUTH_PERMISSION_TIER", "p2")

    settings = AppSettings.from_env()
    principal = LocalPrincipalProvider.from_settings(settings).authenticate(
        peer_host="::1",
        headers=_authorization(),
    )

    assert settings.admin_token == FAKE_TOKEN
    assert settings.local_auth_permission_tier is PermissionTier.P2
    assert principal.tenant_id == "environment-tenant"
    assert principal.principal_id == "environment-user"
    assert principal.permission_tier is PermissionTier.P2


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOCAL_AUTH_PRINCIPAL_ID", ""),
        ("LOCAL_AUTH_PERMISSION_TIER", "P9"),
    ],
)
def test_invalid_local_authority_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        AppSettings.from_env()


def test_local_token_is_excluded_from_configuration_representations() -> None:
    config = _config()
    settings = AppSettings(admin_token=FAKE_TOKEN)

    assert FAKE_TOKEN not in repr(config)
    assert FAKE_TOKEN not in repr(settings)
    assert FAKE_TOKEN not in repr(LocalPrincipalProvider(config))


def test_loopback_helper_has_no_dns_or_forwarded_input_surface() -> None:
    checks: dict[str | None, bool] = {
        "127.0.0.1": True,
        "::1": True,
        "localhost": False,
        "127.0.0.1:443": False,
        "203.0.113.8": False,
        None: False,
    }

    assert {peer: is_loopback_peer(peer) for peer in checks} == checks
