"""Structured errors shared by API, workers, and guardrails."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from personal_assistant.domain.common.privacy import (
    redact_error_context,
    redact_error_message,
    redacted_text_metadata,
    public_error_message,
    safe_optional_identifier,
)


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

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    field: str | None = Field(default=None, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _enforce_public_message(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_message = data.get("message", "")
        public_message = public_error_message(data.get("code"), raw_message)
        raw_context = data.get("context")
        context = dict(raw_context) if isinstance(raw_context, dict) else {}
        if public_message != str(raw_message).strip():
            for key, metadata_value in redacted_text_metadata(raw_message).items():
                context.setdefault(key, metadata_value)
        data["context"] = context
        data["message"] = public_message
        return data

    @field_validator("message", mode="before")
    @classmethod
    def _validate_message(cls, value: object) -> str:
        return redact_error_message(value)

    @field_validator("field", mode="before")
    @classmethod
    def _validate_field(cls, value: object | None) -> str | None:
        return safe_optional_identifier(value)

    @field_validator("context", mode="before")
    @classmethod
    def _validate_context(cls, value: object) -> dict[str, Any]:
        return redact_error_context(value)

    @field_serializer("message")
    def _serialize_message(self, value: str) -> str:
        return public_error_message(self.code, value)

    @field_serializer("field")
    def _serialize_field(self, value: str | None) -> str | None:
        return safe_optional_identifier(value)

    @field_serializer("context")
    def _serialize_context(self, value: dict[str, Any]) -> dict[str, Any]:
        return redact_error_context(value)


class ErrorResponse(BaseModel):
    """Serializable error response."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_assignment=True
    )

    error: ErrorDetail = Field(repr=False)
    request_id: UUID = Field(default_factory=uuid4)
    tenant_id: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_serializer("error")
    def _serialize_error(self, value: object) -> dict[str, Any]:
        try:
            detail = (
                value
                if isinstance(value, ErrorDetail)
                else ErrorDetail.model_validate(value)
            )
        except ValidationError:
            detail = ErrorDetail(
                code=ErrorCode.INTERNAL_ERROR,
                message="invalid structured error",
            )
        return detail.model_dump(mode="json")

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _validate_tenant_id(cls, value: object | None) -> str | None:
        return safe_optional_identifier(value)

    @field_serializer("tenant_id")
    def _serialize_tenant_id(self, value: str | None) -> str | None:
        return safe_optional_identifier(value)


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
        super().__init__(self.response.error.message)

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
