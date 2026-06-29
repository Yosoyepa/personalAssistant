"""Hexagonal ports for the assistant runtime."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.application.dto.runtime import (
    AgentResult,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    AudioTranscriptionRequest,
    AudioTranscriptionResult,
    ApprovalRequest,
    ChannelMessage,
    LLMRequest,
    LLMResult,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.domain.common.permissions import ApprovalGrant
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.memory.models import MemoryKind, MemoryRecord
from personal_assistant.application.dto.tracing import TraceEvent


class LLMProvider(Protocol):
    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        """Return schema-shaped data from a bounded LLM call."""


class AudioTranscriptionProvider(Protocol):
    def transcribe(
        self,
        request: AudioTranscriptionRequest,
        *,
        budget: TokenBudget,
    ) -> AudioTranscriptionResult:
        """Return text from a bounded audio transcription call."""


class AudioSynthesisProvider(Protocol):
    def synthesize(
        self,
        request: AudioSynthesisRequest,
        *,
        budget: TokenBudget,
    ) -> AudioSynthesisResult:
        """Return audio bytes from bounded text-to-speech synthesis."""


class ToolPort(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        """Describe the tool contract, side effects, schemas, and tier."""

    def execute(self, call: ToolCall, *, principal: Principal, approval: ApprovalGrant | None = None) -> ToolResult:
        """Execute an allowlisted tool call."""


class MemoryPort(Protocol):
    def add(
        self,
        principal: Principal,
        *,
        kind: MemoryKind,
        text: str,
        source: str,
        confirmed: bool = False,
    ) -> MemoryRecord:
        """Persist one tenant-scoped memory record."""

    def retrieve(
        self,
        principal: Principal,
        *,
        query: str,
        kind: MemoryKind | None = None,
        confirmed_only: bool = True,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Retrieve tenant-scoped memory context."""

    def list_for_tenant(self, principal: Principal) -> list[MemoryRecord]:
        """List memory records visible to the authenticated principal."""


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
