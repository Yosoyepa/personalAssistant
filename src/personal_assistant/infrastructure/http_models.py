"""Pydantic request/response models for the local HTTP runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus, ApprovalStatus
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    service: str


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "pending", "error"]]
    pending_migrations: list[str] = Field(default_factory=list)
    detail: str | None = None


class DeliveryCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    claimed: int = Field(ge=0)
    sending: int = Field(ge=0)
    published: int = Field(ge=0)
    failed: int = Field(ge=0)
    uncertain: int = Field(ge=0)


class OperationalHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liveness: Literal["ok"] = "ok"
    readiness: Literal["ready", "not_ready"]
    worker: Literal["disabled", "ok", "missing", "stale", "error"]
    metrics: Literal["ok", "error"]


class AdminMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counts: DeliveryCountsResponse
    health: OperationalHealthResponse


class GuardrailCategoryMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int = Field(ge=0)
    flagged: int = Field(ge=0)
    blocked: int = Field(ge=0)


class AdminGuardrailMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int = Field(ge=0)
    allowed: int = Field(ge=0)
    flagged: int = Field(ge=0)
    blocked: int = Field(ge=0)
    hit_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    categories: dict[str, GuardrailCategoryMetricsResponse] = Field(
        default_factory=dict
    )


class ReminderCommandRequest(BaseModel):
    """HTTP transport request for the reminder workflow.

    Tenant and principal fields are deliberately absent; they come from the
    authenticated HTTP boundary and are converted into a trusted Principal.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: Literal["telegram", "whatsapp"] = "telegram"
    recipient: str = Field(min_length=1)
    now: datetime
    timezone: str = "America/Bogota"
    idempotency_key: str | None = None

    def to_workflow_input(
        self, *, approval: ApprovalGrant | None = None
    ) -> ReminderWorkflowInput:
        return ReminderWorkflowInput(
            message_id=self.message_id,
            source_event_id=self.source_event_id,
            conversation_id=self.conversation_id,
            text=self.text,
            channel=self.channel,
            recipient=self.recipient,
            now=self.now,
            timezone=self.timezone,
            idempotency_key=self.idempotency_key,
            approval=approval,
        )


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=500)


class ApprovalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    action: str
    resource: str
    permission_tier: PermissionTier
    reason: str
    status: ApprovalStatus
    created_at: datetime


class ReminderCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    tenant_id_source: Literal["principal"] = "principal"
    status: AgentStatus
    intent: str
    reply: str
    source_event_id: str
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    timezone: str
    clarification_reason: str | None = None
    clarification_reply_id: str | None = None
    clarification_reply_version: str | None = None
    approval_required: bool = False
    approval: ApprovalView | None = None
    calendar_event_id: str | None = None
    reminder_id: str | None = None
    reused: bool = False
    trace_ids: list[str] = Field(default_factory=list)


class ApprovalDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    status: ApprovalStatus
    result: ReminderCommandResponse | None = None


class TelegramWebhookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    status: AgentStatus
    reply: str
    sent: bool = False
    audio_sent: bool = False
    approval_id: str | None = None
    command: str | None = None
