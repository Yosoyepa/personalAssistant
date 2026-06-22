"""Structured errors shared by API, workers, and guardrails."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, Enum):
    VALIDATION_FAILED = "validation_failed"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    TENANT_REQUIRED = "tenant_required"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    PII_DETECTED = "pii_detected"
    PROMPT_INJECTION_DETECTED = "prompt_injection_detected"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INTERNAL_ERROR = "internal_error"


class ErrorDetail(BaseModel):
    """Machine-readable error payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    field: str | None = Field(default=None, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Serializable error response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    error: ErrorDetail
    request_id: UUID = Field(default_factory=uuid4)
    tenant_id: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssistantError(Exception):
    """Exception carrying a structured ErrorResponse."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        field: str | None = None,
        context: dict[str, Any] | None = None,
        request_id: UUID | None = None,
        tenant_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.response = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                field=field,
                context=context or {},
            ),
            request_id=request_id or uuid4(),
            tenant_id=tenant_id,
            retryable=retryable,
        )
        super().__init__(message)

    @property
    def code(self) -> ErrorCode:
        return self.response.error.code

    def model_dump(self) -> dict[str, Any]:
        return self.response.model_dump(mode="json")


def error_response(
    code: ErrorCode,
    message: str,
    *,
    field: str | None = None,
    context: dict[str, Any] | None = None,
    tenant_id: str | None = None,
    retryable: bool = False,
) -> ErrorResponse:
    """Create a structured error response without raising."""

    return ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            field=field,
            context=context or {},
        ),
        tenant_id=tenant_id,
        retryable=retryable,
    )
