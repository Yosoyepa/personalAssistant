"""Permission tiers and authorization helpers.

The tier enum is intentionally small and serializes as P0-P6 so services can
share it across API, worker, and policy boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, computed_field, field_validator, model_validator

from personal_assistant.shared.errors import AssistantError, ErrorCode

if TYPE_CHECKING:
    from personal_assistant.shared.schemas import Principal


class PermissionTier(str, Enum):
    """Risk tier for an action.

    P0 is public/read-only. P6 is destructive, privileged, or legally sensitive.
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"
    P6 = "P6"

    @property
    def rank(self) -> int:
        return int(self.value[1])

    def allows(self, required: "PermissionTier") -> bool:
        return self.rank >= required.rank


TIER_DESCRIPTIONS: dict[PermissionTier, str] = {
    PermissionTier.P0: "Public or already-authorized read-only action.",
    PermissionTier.P1: "Low-risk personal context read.",
    PermissionTier.P2: "Limited write or reversible change.",
    PermissionTier.P3: "External write or moderate data-impacting action.",
    PermissionTier.P4: "Financial action.",
    PermissionTier.P5: "External communication or high-impact notification.",
    PermissionTier.P6: "Destructive, privileged, or legally binding operation.",
}


class PermissionGrant(BaseModel):
    """Named permission assigned to a principal."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    tier: PermissionTier

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("permission name is required")
        return normalized


class PermissionRequest(BaseModel):
    """Permission check input for tools, APIs, and workers."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=1, max_length=120)
    resource: str = Field(default="*", min_length=1, max_length=200)
    required_tier: PermissionTier = PermissionTier.P0
    permission: str | None = Field(default=None, max_length=120)

    @field_validator("action", "resource", "permission")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized


class PermissionDecision(BaseModel):
    """Result of evaluating a permission request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    required_tier: PermissionTier
    principal_tier: PermissionTier
    reason: str = Field(min_length=1, max_length=300)


class ApprovalGrant(BaseModel):
    """Trusted approval produced by an out-of-band approval service.

    Direct model construction is intentionally not trusted. Use `issue` from a
    trusted runtime boundary in local tests/dev, and replace it with a persisted
    approval-service lookup in production.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    approval_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: str = Field(default="*", min_length=1)
    tier: PermissionTier
    approved: bool = True
    expires_at: datetime | None = None
    request_hash: str | None = None
    _trusted_source: str | None = PrivateAttr(default=None)

    @computed_field
    @property
    def is_trusted(self) -> bool:
        return self._trusted_source is not None

    @model_validator(mode="after")
    def validate_expiry_timezone(self) -> "ApprovalGrant":
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return self

    @classmethod
    def issue(
        cls,
        *,
        principal: Any,
        action: str,
        tier: PermissionTier | str,
        resource: str = "*",
        approval_id: str = "local-approved",
        expires_at: datetime | None = None,
        request_hash: str | None = None,
    ) -> "ApprovalGrant":
        grant = cls(
            approval_id=approval_id,
            tenant_id=getattr(principal, "tenant_id"),
            principal_id=getattr(principal, "principal_id"),
            action=action,
            resource=resource,
            tier=coerce_tier(tier),
            expires_at=expires_at,
            request_hash=request_hash,
        )
        grant._trusted_source = "trusted_runtime"
        return grant


def coerce_tier(value: PermissionTier | str) -> PermissionTier:
    """Convert a serialized tier into a PermissionTier."""

    if isinstance(value, PermissionTier):
        return value
    return PermissionTier(value)


def max_tier(*tiers: PermissionTier | str) -> PermissionTier:
    """Return the highest tier from a collection."""

    if not tiers:
        return PermissionTier.P0
    return max((coerce_tier(tier) for tier in tiers), key=lambda tier: tier.rank)


def evaluate_permission(principal: Any, request: PermissionRequest) -> PermissionDecision:
    """Evaluate a request against a principal-like object.

    The principal is intentionally structural: any object with `permission_tier`
    and optional `permissions` works, which avoids importing schemas here.
    """

    if not getattr(principal, "is_trusted", False):
        return PermissionDecision(
            allowed=False,
            required_tier=request.required_tier,
            principal_tier=PermissionTier.P0,
            reason="principal must be derived from verified auth",
        )

    principal_tier = coerce_tier(getattr(principal, "permission_tier", PermissionTier.P0))
    if not principal_tier.allows(request.required_tier):
        return PermissionDecision(
            allowed=False,
            required_tier=request.required_tier,
            principal_tier=principal_tier,
            reason="principal tier is below the required tier",
        )

    if request.permission is None:
        return PermissionDecision(
            allowed=True,
            required_tier=request.required_tier,
            principal_tier=principal_tier,
            reason="tier requirement satisfied",
        )

    grants = getattr(principal, "permissions", frozenset())
    matched_grant = next(
        (
            grant
            for grant in grants
            if isinstance(grant, PermissionGrant)
            and grant.name == request.permission
            and grant.tier.allows(request.required_tier)
        ),
        None,
    )
    matched_legacy_grant = any(
        not isinstance(grant, PermissionGrant) and str(grant).strip().lower() == request.permission for grant in grants
    )
    allowed = matched_grant is not None or matched_legacy_grant
    return PermissionDecision(
        allowed=allowed,
        required_tier=request.required_tier,
        principal_tier=principal_tier,
        reason="permission grant satisfied" if allowed else "missing permission grant at required tier",
    )


def require_permission(principal: Any, request: PermissionRequest) -> PermissionDecision:
    """Return a decision or raise AssistantError if permission is denied."""

    decision = evaluate_permission(principal, request)
    if not decision.allowed:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            decision.reason,
            tenant_id=getattr(principal, "tenant_id", None),
            context={
                "action": request.action,
                "resource": request.resource,
                "required_tier": request.required_tier.value,
                "principal_tier": decision.principal_tier.value,
            },
        )
    return decision


def require_approval(
    *,
    principal: Any,
    tier: PermissionTier | str,
    approval: ApprovalGrant | None,
    action: str,
    resource: str = "*",
    request_hash: str | None = None,
) -> PermissionDecision:
    """Require permission and explicit approval for P3+ actions.

    Approval is a structured grant from a trusted runtime boundary. Raw strings
    from model/tool input are never accepted as approval.
    """

    required_tier = coerce_tier(tier)
    decision = require_permission(
        principal,
        PermissionRequest(action=action, resource=resource, required_tier=required_tier),
    )
    if required_tier.rank >= PermissionTier.P3.rank and approval is None:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "trusted approval grant required for P3+ action",
            tenant_id=getattr(principal, "tenant_id", None),
            context={"action": action, "resource": resource, "required_tier": required_tier.value},
        )
    if required_tier.rank < PermissionTier.P3.rank:
        return decision

    assert approval is not None
    now = datetime.now(UTC)
    approval_errors: list[str] = []
    if not approval.is_trusted:
        approval_errors.append("approval grant is not trusted")
    if not approval.approved:
        approval_errors.append("approval grant is not approved")
    if approval.tenant_id != getattr(principal, "tenant_id", None):
        approval_errors.append("approval tenant mismatch")
    if approval.principal_id != getattr(principal, "principal_id", None):
        approval_errors.append("approval principal mismatch")
    if approval.action != action:
        approval_errors.append("approval action mismatch")
    if approval.resource != resource:
        approval_errors.append("approval resource mismatch")
    if not approval.tier.allows(required_tier):
        approval_errors.append("approval tier too low")
    if approval.expires_at is not None and approval.expires_at <= now:
        approval_errors.append("approval expired")
    if request_hash is not None and approval.request_hash != request_hash:
        approval_errors.append("approval request hash mismatch")
    if approval_errors:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "approval grant rejected",
            tenant_id=getattr(principal, "tenant_id", None),
            context={
                "action": action,
                "resource": resource,
                "required_tier": required_tier.value,
                "approval_errors": approval_errors,
            },
        )
    return decision


def __getattr__(name: str) -> Any:
    """Provide backward-compatible lazy access to Principal without a cycle."""

    if name == "Principal":
        from personal_assistant.shared.schemas import Principal

        return Principal
    raise AttributeError(name)


__all__ = [
    "PermissionTier",
    "TIER_DESCRIPTIONS",
    "PermissionGrant",
    "PermissionRequest",
    "PermissionDecision",
    "ApprovalGrant",
    "Principal",
    "coerce_tier",
    "max_tier",
    "evaluate_permission",
    "require_permission",
    "require_approval",
]
