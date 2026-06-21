"""Shared Pydantic schemas for tenant-aware assistant services."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)

from personal_assistant.shared.permissions import PermissionGrant, PermissionTier


class SharedModel(BaseModel):
    """Base model config for shared contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        populate_by_name=True,
        use_enum_values=False,
    )


class AuthClaims(SharedModel):
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


class TokenBudget(SharedModel):
    """Token accounting for a request, task, or worker step."""

    limit: int = Field(gt=0, le=10_000_000)
    used: int = Field(default=0, ge=0)
    reserved: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_budget(self) -> "TokenBudget":
        if self.used + self.reserved > self.limit:
            raise ValueError("used plus reserved tokens cannot exceed limit")
        return self

    @computed_field
    @property
    def remaining(self) -> int:
        return self.limit - self.used - self.reserved

    def can_spend(self, tokens: int) -> bool:
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        return tokens <= self.remaining

    def spend(self, tokens: int) -> "TokenBudget":
        if not self.can_spend(tokens):
            raise ValueError("token budget exceeded")
        return self.model_copy(update={"used": self.used + tokens})

    def reserve(self, tokens: int) -> "TokenBudget":
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        if tokens > self.remaining:
            raise ValueError("token budget exceeded")
        return self.model_copy(update={"reserved": self.reserved + tokens})


class Principal(SharedModel):
    """Authenticated actor.

    `tenant_id` is required and should be populated only from verified auth
    claims, never from request body data supplied by the user.
    """

    principal_id: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("principal_id", "actor_id"),
        serialization_alias="principal_id",
    )
    tenant_id: str = Field(min_length=1, max_length=120)
    auth_subject: str = Field(min_length=1, max_length=200)
    auth_provider: str | None = Field(default=None, max_length=120)
    permission_tier: PermissionTier = PermissionTier.P0
    permissions: frozenset[PermissionGrant] = Field(default_factory=frozenset)
    scopes: frozenset[str] = Field(default_factory=frozenset)
    _trusted_source: str | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def derive_identity_fields(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        values = dict(data)
        identity = values.get("principal_id") or values.get("actor_id") or values.get("sub") or values.get("subject")
        if identity and "principal_id" not in values:
            values["principal_id"] = identity
        if identity and "auth_subject" not in values:
            values["auth_subject"] = identity
        values.pop("actor_id", None)
        values.pop("sub", None)
        values.pop("subject", None)
        return values

    @field_validator("principal_id", "tenant_id", "auth_subject", "auth_provider")
    @classmethod
    def reject_blank_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("identity field cannot be blank")
        return value

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: Any) -> frozenset[str]:
        return AuthClaims.normalize_scopes(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def normalize_permissions(cls, value: Any) -> frozenset[PermissionGrant]:
        if value is None:
            return frozenset()
        return frozenset(PermissionGrant.model_validate(item) for item in value)

    @computed_field
    @property
    def actor_id(self) -> str:
        return self.principal_id

    @computed_field
    @property
    def is_trusted(self) -> bool:
        return self._trusted_source is not None

    @classmethod
    def from_auth_claims(
        cls,
        claims: AuthClaims | Mapping[str, Any],
        *,
        principal_id: str | None = None,
        auth_provider: str | None = None,
        permission_tier: PermissionTier = PermissionTier.P0,
        permissions: set[PermissionGrant] | frozenset[PermissionGrant] | None = None,
    ) -> "Principal":
        verified_claims = claims if isinstance(claims, AuthClaims) else AuthClaims.from_mapping(claims)
        principal = cls(
            principal_id=principal_id or verified_claims.subject,
            tenant_id=verified_claims.tenant_id,
            auth_subject=verified_claims.subject,
            auth_provider=auth_provider or verified_claims.issuer,
            permission_tier=permission_tier,
            permissions=permissions or frozenset(),
            scopes=verified_claims.scopes,
        )
        principal._trusted_source = "verified_auth"
        return principal

    @classmethod
    def for_test(
        cls,
        *,
        principal_id: str,
        tenant_id: str,
        permission_tier: PermissionTier = PermissionTier.P0,
    ) -> "Principal":
        principal = cls(
            principal_id=principal_id,
            tenant_id=tenant_id,
            auth_subject=principal_id,
            auth_provider="test",
            permission_tier=permission_tier,
        )
        principal._trusted_source = "test"
        return principal


def require_trusted_principal(principal: Principal) -> None:
    if not principal.is_trusted:
        from personal_assistant.shared.errors import AssistantError, ErrorCode

        raise AssistantError(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "principal must be derived from verified auth",
            tenant_id=principal.tenant_id,
        )


class RequestContext(SharedModel):
    """Per-request context shared by API and worker layers."""

    request_id: UUID = Field(default_factory=uuid4)
    principal: Principal
    token_budget: TokenBudget
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    channel: Literal["api", "worker", "cli", "system"] = "api"
    metadata: dict[str, str] = Field(default_factory=dict)

    @computed_field
    @property
    def tenant_id(self) -> str:
        return self.principal.tenant_id
