"""Framework-light tool contract models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.shared.permissions import PermissionTier


class SideEffect(str, Enum):
    none = "none"
    internal_write = "internal_write"
    external_write = "external_write"
    financial = "financial"
    communication = "communication"
    destructive = "destructive"


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    side_effect: SideEffect = SideEffect.none
    permission_tier: PermissionTier = PermissionTier.P0
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    failure_cases: list[str] = Field(default_factory=list)
    audit_requirements: list[str] = Field(default_factory=list)
    tenant_isolation: str = "tenant_id must come from Principal and must not be model supplied"
    idempotency_required: bool = False
    approval_required: bool = False
    mcp_exposable: bool = False


CALENDAR_CREATE_CONTRACT = ToolContract(
    name="calendar.create_event",
    purpose="Create an idempotent calendar event for the authenticated tenant after approval.",
    side_effect=SideEffect.external_write,
    permission_tier=PermissionTier.P3,
    preconditions=[
        "principal.tenant_id is present",
        "idempotency_key is present",
        "trusted ApprovalGrant is present for P3 execution",
    ],
    postconditions=[
        "event belongs to principal.tenant_id",
        "duplicate idempotency_key returns existing event",
    ],
    failure_cases=[
        "permission denied",
        "approval grant missing or untrusted",
        "invalid datetime",
    ],
    audit_requirements=[
        "tenant_id",
        "principal_id",
        "event_id",
        "idempotency_key",
        "approval decision",
    ],
    idempotency_required=True,
    approval_required=True,
    mcp_exposable=True,
)

NOTIFICATION_SEND_CONTRACT = ToolContract(
    name="notification.send",
    purpose="Send an idempotent notification only after communication approval.",
    side_effect=SideEffect.communication,
    permission_tier=PermissionTier.P5,
    preconditions=[
        "principal.tenant_id is present",
        "recipient is the approved recipient",
        "trusted ApprovalGrant is present for P5 execution",
    ],
    postconditions=[
        "message id recorded",
        "duplicate idempotency_key returns existing notification",
    ],
    failure_cases=[
        "permission denied",
        "approval grant missing or untrusted",
        "delivery adapter unavailable",
    ],
    audit_requirements=[
        "tenant_id",
        "principal_id",
        "recipient",
        "idempotency_key",
        "approval decision",
    ],
    idempotency_required=True,
    approval_required=True,
    mcp_exposable=True,
)
