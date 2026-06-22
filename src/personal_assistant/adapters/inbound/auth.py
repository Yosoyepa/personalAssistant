"""Inbound auth claim mapping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, field_validator

from personal_assistant.application.dto.base import ApplicationDTO
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionGrant, PermissionTier


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
        return frozenset(str(item).strip().lower() for item in items if str(item).strip())

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
            scopes=scopes,
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
    verified_claims = claims if isinstance(claims, AuthClaims) else AuthClaims.from_mapping(claims)
    principal = Principal(
        principal_id=principal_id or verified_claims.subject,
        tenant_id=verified_claims.tenant_id,
        auth_subject=verified_claims.subject,
        auth_provider=auth_provider or verified_claims.issuer,
        permission_tier=permission_tier,
        permissions=permissions or frozenset(),
        scopes=verified_claims.scopes,
    )
    principal.mark_trusted("verified_auth")
    return principal
