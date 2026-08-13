"""Privacy redaction helpers for admin dashboard payloads."""

from __future__ import annotations

from typing import Any

from personal_assistant.domain.common.privacy import (
    REDACTED,
    redact_trace_mapping,
    redacted_text_metadata,
    safe_category,
)


def _payload_error(data: dict[str, Any]) -> tuple[bool, Any]:
    for key, value in data.items():
        if isinstance(key, str):
            normalized = "".join(
                character
                for character in key.casefold()
                if character.isalnum()
            )
            if normalized == "error":
                return True, value
    return False, None


def _redacted_admin_payload(data: dict[str, Any]) -> dict[str, Any]:
    safe_data = redact_trace_mapping(data)
    found, value = _payload_error(data)
    if found:
        metadata = redacted_text_metadata(value)
        safe_data["error_length"] = metadata["message_length"]
        safe_data["error_sha256"] = metadata["message_sha256"]
    return safe_data


def _redacted_failure_message(
    data: dict[str, Any], *, fallback: str = ""
) -> str:
    found, _ = _payload_error(data)
    if found:
        return REDACTED
    return safe_category(fallback) or ""
