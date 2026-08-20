"""HTTP error handling and status mapping for the local assistant runtime."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from personal_assistant.domain.common.exceptions import (
    AssistantError,
    ErrorCode,
    error_response,
)


def _status_for_error(code: ErrorCode) -> int:
    return {
        ErrorCode.AUTHENTICATION_REQUIRED: 401,
        ErrorCode.TENANT_REQUIRED: 400,
        ErrorCode.PERMISSION_DENIED: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.CONFLICT: 409,
        ErrorCode.TOKEN_BUDGET_EXCEEDED: 429,
        ErrorCode.VALIDATION_FAILED: 422,
        ErrorCode.GUARDRAIL_BLOCKED: 422,
        ErrorCode.PII_DETECTED: 422,
        ErrorCode.PROMPT_INJECTION_DETECTED: 422,
        ErrorCode.PAYLOAD_TOO_LARGE: 413,
    }.get(code, 500)


def register_exception_handlers(app: FastAPI) -> None:
    """Register uniform JSON exception handlers on the FastAPI application."""

    @app.exception_handler(AssistantError)
    async def handle_assistant_error(_: Any, exc: AssistantError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for_error(exc.code),
            content=jsonable_encoder(exc.model_dump()),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Any, exc: RequestValidationError
    ) -> JSONResponse:
        response = error_response(
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            context={"errors": exc.errors()},
        )
        return JSONResponse(
            status_code=422, content=jsonable_encoder(response.model_dump(mode="json"))
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Any, exc: ValidationError) -> JSONResponse:
        response = error_response(
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            context={"errors": exc.errors()},
        )
        return JSONResponse(
            status_code=422, content=jsonable_encoder(response.model_dump(mode="json"))
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Any, exc: ValueError) -> JSONResponse:
        response = error_response(ErrorCode.VALIDATION_FAILED, str(exc))
        return JSONResponse(
            status_code=422, content=jsonable_encoder(response.model_dump(mode="json"))
        )
