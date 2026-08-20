"""Local and remote admin authentication provider."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
from typing import Protocol

from personal_assistant.adapters.inbound.auth_peer import (
    _BEARER_AUTHORIZATION,
    _LOCAL_AUTH_SOURCE,
    _invalid_local_credentials,
    _strict_bearer_token,
    _validate_configured_token,
    _validate_identity_setting,
    is_loopback_peer,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier


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

    @property
    def admin_allow_remote(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class LocalPrincipalConfig:
    """Immutable authority for local and remote admin HTTP authentication.

    The bearer token is excluded from representations. Tenant, principal, and
    tier live here rather than in the request so caller-controlled data cannot
    widen the resulting authority.
    """

    token: str | None = field(repr=False)
    tenant_id: str
    principal_id: str
    permission_tier: PermissionTier
    allow_remote: bool = False

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
    Authorization header and session cookies are inspected; all identity-like
    headers are intentionally ignored.
    """

    __slots__ = (
        "_allow_remote",
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
        self._allow_remote = config.allow_remote

    @property
    def allow_remote(self) -> bool:
        """Return whether remote (non-loopback) peers are allowed with valid credentials."""
        return self._allow_remote

    @classmethod
    def from_settings(cls, settings: LocalPrincipalSettings) -> LocalPrincipalProvider:
        """Build from server settings without retaining their bearer token."""
        return cls(
            LocalPrincipalConfig(
                token=settings.admin_token,
                tenant_id=settings.tenant_id,
                principal_id=settings.local_auth_principal_id,
                permission_tier=settings.local_auth_permission_tier,
                allow_remote=getattr(settings, "admin_allow_remote", False),
            )
        )

    def verify_token(self, token: str | None) -> bool:
        """Validate a candidate bearer token using constant-time digest comparison."""
        if not isinstance(token, str) or not token:
            return False
        if _BEARER_AUTHORIZATION.fullmatch(f"Bearer {token}") is None:
            return False
        try:
            supplied_digest = sha256(token.encode("ascii")).digest()
        except UnicodeEncodeError:
            return False
        return compare_digest(supplied_digest, self._expected_token_digest)

    def authenticate(
        self,
        *,
        peer_host: str | None,
        headers: Mapping[str, str],
        cookies: Mapping[str, str] | None = None,
    ) -> Principal:
        """Return a trusted principal or fail closed.

        The mapping is used only to locate exactly one Authorization header or
        cookie. Tenant, principal, permission, scope, host, and forwarding
        headers have no effect on the returned principal.
        """
        is_loopback = is_loopback_peer(peer_host)
        if not self._allow_remote and not is_loopback:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "local authentication requires a loopback peer",
            )

        supplied_token = _strict_bearer_token(headers)
        if supplied_token is None and cookies is not None:
            cookie_candidate = cookies.get("admin_token")
            if isinstance(cookie_candidate, str) and cookie_candidate:
                supplied_token = cookie_candidate

        if supplied_token is None:
            raise _invalid_local_credentials()

        if not self.verify_token(supplied_token):
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
