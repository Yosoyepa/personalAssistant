"""Runtime schemas for LLM and tool execution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from personal_assistant.domain.common.permissions import PermissionTier


class AgentStatus(str, Enum):
    completed = "completed"
    needs_clarification = "needs_clarification"
    declined = "declined"
    escalated = "escalated"
    failed = "failed"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


class SideEffectType(str, Enum):
    none = "none"
    draft = "draft"
    internal_write = "internal_write"
    external_write = "external_write"
    financial = "financial"
    communication = "communication"
    destructive = "destructive"


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(min_length=1)
    schema_name: str = Field(min_length=1)
    max_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class LLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    data: dict[str, Any]
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class AudioTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    data: bytes = Field(min_length=1)
    language: str | None = Field(default="es", min_length=2, max_length=16)
    prompt: str | None = Field(default=None, min_length=1, max_length=500)


class AudioTranscriptionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str
    model: str
    text: str = Field(min_length=1)
    input_tokens: int = Field(default=0, ge=0)


class AudioSynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=10_000)
    voice_id: str | None = Field(default=None, min_length=1)
    audio_format: Literal["mp3", "wav", "flac"] = "mp3"
    language_boost: str | None = Field(default="Spanish", min_length=2, max_length=64)


class AudioSynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str
    model: str
    audio: bytes = Field(min_length=1)
    content_type: str = Field(min_length=1)
    filename_extension: str = Field(min_length=1)
    characters: int = Field(default=0, ge=0)
    trace_id: str | None = None


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    permission_tier: PermissionTier = PermissionTier.P0
    side_effect: SideEffectType = SideEffectType.none
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    idempotent: bool = True
    timeout_seconds: float = Field(default=10.0, gt=0)

    @computed_field
    @property
    def approval_required(self) -> bool:
        return self.permission_tier.rank >= PermissionTier.P3.rank


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    permission_tier: PermissionTier = PermissionTier.P0
    idempotency_key: str = Field(default_factory=lambda: f"tool_{uuid4().hex}")

    @field_validator("name")
    @classmethod
    def forbid_inactive_protocol_tools(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.startswith(("mcp.", "a2a.")):
            raise ValueError("A2A and MCP tools are prepared but inactive in the MVP runtime")
        return value

    @computed_field
    @property
    def approval_required(self) -> bool:
        return self.permission_tier.rank >= PermissionTier.P3.rank


class IntentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: str
    confidence: float = Field(ge=0, le=1)


class ResponseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reply_draft: str
    requires_dispatch: bool = True
    dispatch_policy: Literal["direct_reply_to_principal", "requires_review", "none"] = "requires_review"


class EscalationState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    required: bool = False
    reason: str = "none"


class TraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    events_written: int = Field(ge=0)


class PersonalAssistantRunResult(BaseModel):
    """Contract-level result artifact for one inbound request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str
    agent_id: str = "personal_assistant"
    status: AgentStatus
    principal_id: str
    tenant_id: str
    tenant_id_source: Literal["principal"] = "principal"
    channel: Literal["telegram", "whatsapp"]
    intent: IntentSummary
    response: ResponseDraft
    state_changes: list[dict[str, Any]] = Field(default_factory=list)
    outbox_events: list[dict[str, Any]] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    guardrail_results: list[dict[str, str]] = Field(default_factory=list)
    escalation: EscalationState = Field(default_factory=EscalationState)
    trace: TraceSummary


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    reused: bool = False


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=1, max_length=120)
    resource: str = Field(min_length=1, max_length=240)
    permission_tier: PermissionTier
    reason: str = Field(min_length=1, max_length=500)
    tenant_id: str = Field(min_length=1, max_length=120)
    principal_id: str = Field(min_length=1, max_length=200)
    args: dict[str, Any] = Field(default_factory=dict)
    approval_id: str = Field(default_factory=lambda: f"apr_{uuid4().hex}")
    status: ApprovalStatus = ApprovalStatus.pending
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    @computed_field
    @property
    def approval_required(self) -> bool:
        return self.permission_tier.rank >= PermissionTier.P3.rank


class ChannelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recipient: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: Literal["telegram", "whatsapp", "inbox", "system"] = "inbox"
    idempotency_key: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    approval_request: ApprovalRequest | None = None


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    agent_id: str
    status: AgentStatus
    tenant_id: str
    reply: str
    approval_required: bool = False
    state_changes: list[dict[str, Any]] = Field(default_factory=list)
    outbox_events: list[dict[str, Any]] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
