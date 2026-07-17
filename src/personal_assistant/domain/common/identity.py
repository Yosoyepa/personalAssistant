"""Tenant-aware identity domain models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import (
    AliasChoices,
    Field,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)

from personal_assistant.domain.common.base import DomainModel
from personal_assistant.domain.common.permissions import PermissionGrant, PermissionTier


def normalize_scopes(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        items = value.split()
    else:
        items = value
    return frozenset(str(item).strip().lower() for item in items if str(item).strip())


class Principal(DomainModel):
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
        return normalize_scopes(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def normalize_permissions(cls, value: Any) -> frozenset[PermissionGrant]:
        if value is None:
            return frozenset()
        return frozenset(PermissionGrant.model_validate(item) for item in value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def actor_id(self) -> str:
        return self.principal_id

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_trusted(self) -> bool:
        return self._trusted_source is not None

    def mark_trusted(self, source: str) -> None:
        if not source.strip():
            raise ValueError("trusted source cannot be blank")
        self._trusted_source = source

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
        principal.mark_trusted("test")
        return principal


def require_trusted_principal(principal: Principal) -> None:
    if not principal.is_trusted:
        from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode

        raise AssistantError(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "principal must be derived from verified auth",
            tenant_id=principal.tenant_id,
        )
