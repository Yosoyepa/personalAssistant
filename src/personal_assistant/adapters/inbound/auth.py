"""Inbound auth claim mapping."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
from ipaddress import ip_address
from typing import Any, Protocol, cast

from pydantic import Field, field_validator

from personal_assistant.application.dto.base import ApplicationDTO
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionGrant, PermissionTier


_BEARER_AUTHORIZATION = re.compile(
    r"Bearer ([A-Za-z0-9._~+/\-]+=*)",
    flags=re.ASCII | re.IGNORECASE,
)
_LOCAL_AUTH_SOURCE = "local-bearer"


class LocalPrincipalSettings(Protocol):
    """Server-owned settings required by the local principal provider."""

    @property
    def admin_token(self) -> str | None: ...

    @property
    def tenant_id(self) -> str: ...

    @property
    def local_auth_principal_id(self) -> str: ...

    @property
    def local_auth_permission_tier(self) -> PermissionTier: ...


@dataclass(frozen=True, slots=True)
class LocalPrincipalConfig:
    """Immutable authority for local HTTP authentication.

    The bearer token is excluded from representations. Tenant, principal, and
    tier live here rather than in the request so caller-controlled data cannot
    widen the resulting authority.
    """

    token: str | None = field(repr=False)
    tenant_id: str
    principal_id: str
    permission_tier: PermissionTier

    def __post_init__(self) -> None:
        _validate_configured_token(self.token)
        _validate_identity_setting(
            self.tenant_id,
            name="ASSISTANT_TENANT_ID",
            max_length=120,
        )
        _validate_identity_setting(
            self.principal_id,
            name="LOCAL_AUTH_PRINCIPAL_ID",
            max_length=200,
        )
        try:
            permission_tier = PermissionTier(self.permission_tier)
        except (TypeError, ValueError) as exc:
            raise ValueError("LOCAL_AUTH_PERMISSION_TIER must be one of P0-P6") from exc
        object.__setattr__(self, "permission_tier", permission_tier)


class LocalPrincipalProvider:
    """Authenticate a socket peer and derive one server-configured principal.

    ``peer_host`` must come directly from the accepted connection (for example,
    ``request.client.host``), never from ``Forwarded`` or ``X-Forwarded-*``.
    Only the Authorization header is inspected; all identity-like headers are
    intentionally ignored.
    """

    __slots__ = (
        "_expected_token_digest",
        "_permission_tier",
        "_principal_id",
        "_tenant_id",
    )

    def __init__(self, config: LocalPrincipalConfig) -> None:
        token = config.token
        if token is None:  # Defensive: LocalPrincipalConfig already validates.
            raise ValueError("ADMIN_TOKEN must be configured for local authentication")
        self._expected_token_digest = sha256(token.encode("ascii")).digest()
        self._tenant_id = config.tenant_id
        self._principal_id = config.principal_id
        self._permission_tier = config.permission_tier

    @classmethod
    def from_settings(
        cls, settings: LocalPrincipalSettings
    ) -> "LocalPrincipalProvider":
        """Build from server settings without retaining their bearer token."""

        return cls(
            LocalPrincipalConfig(
                token=settings.admin_token,
                tenant_id=settings.tenant_id,
                principal_id=settings.local_auth_principal_id,
                permission_tier=settings.local_auth_permission_tier,
            )
        )

    def authenticate(
        self,
        *,
        peer_host: str | None,
        headers: Mapping[str, str],
    ) -> Principal:
        """Return a trusted principal or fail closed.

        The mapping is used only to locate exactly one Authorization header.
        Tenant, principal, permission, scope, host, and forwarding headers have
        no effect on the returned principal.
        """

        if not is_loopback_peer(peer_host):
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "local authentication requires a loopback peer",
            )

        supplied_token = _strict_bearer_token(headers)
        if supplied_token is None:
            raise _invalid_local_credentials()
        supplied_digest = sha256(supplied_token.encode("ascii")).digest()
        if not compare_digest(supplied_digest, self._expected_token_digest):
            raise _invalid_local_credentials()

        principal = Principal(
            principal_id=self._principal_id,
            tenant_id=self._tenant_id,
            auth_subject=self._principal_id,
            auth_provider=_LOCAL_AUTH_SOURCE,
            permission_tier=self._permission_tier,
        )
        principal.mark_trusted(_LOCAL_AUTH_SOURCE)
        return principal


def is_loopback_peer(peer_host: str | None) -> bool:
    """Accept only a numeric IPv4/IPv6 loopback socket peer."""

    if not isinstance(peer_host, str) or not peer_host:
        return False
    if peer_host != peer_host.strip():
        return False
    try:
        peer_ip = ip_address(peer_host)
    except ValueError:
        return False
    mapped_ipv4 = getattr(peer_ip, "ipv4_mapped", None)
    return peer_ip.is_loopback or (mapped_ipv4 is not None and mapped_ipv4.is_loopback)


def _strict_bearer_token(headers: Mapping[str, str]) -> str | None:
    authorization_values = [
        value
        for name, value in headers.items()
        if isinstance(name, str) and name.lower() == "authorization"
    ]
    if len(authorization_values) != 1:
        return None
    authorization = authorization_values[0]
    if not isinstance(authorization, str):
        return None
    matched = _BEARER_AUTHORIZATION.fullmatch(authorization)
    if matched is None:
        return None
    return matched.group(1)


def _validate_configured_token(token: str | None) -> None:
    if token is None:
        raise ValueError("ADMIN_TOKEN must be configured for local authentication")
    if (
        not isinstance(token, str)
        or _BEARER_AUTHORIZATION.fullmatch(f"Bearer {token}") is None
    ):
        raise ValueError("ADMIN_TOKEN must be a non-empty bearer token")


def _validate_identity_setting(value: str, *, name: str, max_length: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-blank string without outer whitespace")
    if len(value) > max_length or not value.isprintable():
        raise ValueError(f"{name} contains invalid identity text")


def _invalid_local_credentials() -> AssistantError:
    return AssistantError(
        ErrorCode.AUTHENTICATION_REQUIRED,
        "valid local bearer credentials are required",
    )


class AuthClaims(ApplicationDTO):
    """Verified authentication claims used to construct a Principal."""

    subject: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=120)
    issuer: str | None = Field(default=None, max_length=300)
    audience: str | None = Field(default=None, max_length=300)
    scopes: frozenset[str] = Field(default_factory=frozenset)
    raw_claims: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject", "tenant_id", "issuer", "audience")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("value cannot be blank")
        return value

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: Any) -> frozenset[str]:
        if value is None:
            return frozenset()
        if isinstance(value, str):
            items = value.split()
        else:
            items = value
        return frozenset(
            str(item).strip().lower() for item in items if str(item).strip()
        )

    @classmethod
    def from_mapping(cls, claims: Mapping[str, Any]) -> "AuthClaims":
        """Build claims from common identity-provider field names."""

        tenant_id = (
            claims.get("tenant_id")
            or claims.get("tid")
            or claims.get("tenant")
            or claims.get("https://personal-assistant/tenant_id")
        )
        subject = claims.get("sub") or claims.get("subject") or claims.get("user_id")
        scopes = claims.get("scope") or claims.get("scp") or claims.get("scopes") or ()
        return cls(
            subject=str(subject or ""),
            tenant_id=str(tenant_id or ""),
            issuer=claims.get("iss"),
            audience=claims.get("aud"),
            scopes=cast(frozenset[str], scopes),
            raw_claims=dict(claims),
        )


def principal_from_auth_claims(
    claims: AuthClaims | Mapping[str, Any],
    *,
    principal_id: str | None = None,
    auth_provider: str | None = None,
    permission_tier: PermissionTier = PermissionTier.P0,
    permissions: set[PermissionGrant] | frozenset[PermissionGrant] | None = None,
) -> Principal:
    verified_claims = (
        claims if isinstance(claims, AuthClaims) else AuthClaims.from_mapping(claims)
    )
    principal = Principal(
        principal_id=principal_id or verified_claims.subject,
        tenant_id=verified_claims.tenant_id,
        auth_subject=verified_claims.subject,
        auth_provider=auth_provider or verified_claims.issuer,
        permission_tier=permission_tier,
        permissions=cast(frozenset[PermissionGrant], permissions or frozenset()),
        scopes=verified_claims.scopes,
    )
    principal.mark_trusted("verified_auth")
    return principal
