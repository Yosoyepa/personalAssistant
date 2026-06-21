"""Hexagonal ports for the assistant runtime."""

from __future__ import annotations

from typing import Any, Protocol

from personal_assistant.agent_runtime.models import (
    AgentResult,
    ApprovalRequest,
    ChannelMessage,
    LLMRequest,
    LLMResult,
    MemoryRecord,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from personal_assistant.shared.permissions import ApprovalGrant
from personal_assistant.shared.schemas import Principal, TokenBudget
from personal_assistant.shared.tracing import TraceEvent


class LLMProvider(Protocol):
    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        """Return schema-shaped data from a bounded LLM call."""


class ToolPort(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        """Describe the tool contract, side effects, schemas, and tier."""

    def execute(self, call: ToolCall, *, principal: Principal, approval: ApprovalGrant | None = None) -> ToolResult:
        """Execute an allowlisted tool call."""


class MemoryPort(Protocol):
    def retrieve(self, query: str, *, principal: Principal, limit: int = 5) -> list[Any]:
        """Retrieve tenant-scoped memory context."""

    def save(self, record: MemoryRecord, *, principal: Principal) -> MemoryRecord:
        """Persist tenant-scoped memory or workflow state."""


class ChannelAdapter(Protocol):
    def send_text(self, recipient: str, text: str, *, idempotency_key: str) -> ToolResult:
        """Send text through a channel adapter after policy approval."""

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        """Queue a human approval request; never self-approve it."""

    def send_message(self, message: ChannelMessage) -> ToolResult:
        """Deliver a structured channel message."""


class AgentRuntimePort(Protocol):
    def run(self, task: str, *, principal: Principal, budget: TokenBudget) -> AgentResult:
        """Run a bounded assistant task."""

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        """Create a code-enforced P3+ approval gate."""

    def emit_trace(self, event: TraceEvent) -> None:
        """Persist one trace event for audit and replay."""

    def list_tools(self) -> list[ToolDefinition]:
        """Return the active tool allowlist."""
