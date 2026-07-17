"""Fail-closed privacy primitives for trace and structured-error boundaries."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from enum import Enum
from itertools import islice
from typing import Any


REDACTED = "[REDACTED]"
REDACTED_URL = "[REDACTED_URL]"

_MAX_DEPTH = 8
_MAX_ITEMS = 100
_MAX_KEY_LENGTH = 80

_SAFE_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+@-]{0,239}\Z")
_SAFE_CATEGORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+@-]{0,159}\Z")
_SAFE_HASH_RE = re.compile(r"(?:sha256:)?[A-Fa-f0-9]{32,128}\Z")
_URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
_AUTHORITY_CREDENTIAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9._~-]{1,64}):(?:[^@\s/:]{1,128})@"
    r"(?:[A-Za-z0-9.-]+)(?:/[^\s]*)?"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[ _-]?key|access[ _-]?token|refresh[ _-]?token|token|secret|"
    r"password|passwd|authorization|credential|cookie|session)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_PRIVATE_TEXT_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(message|text|transcript|prompt|body|content|audio)\b"
    r"\s*[\"']?\s*[:=]\s*[\"']?([^;\r\n}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_OPAQUE_SECRET_RE = re.compile(
    r"\b(?:sk|ghp|gho|ghu|ghs|github_pat)-?[A-Za-z0-9_-]{18,}\b",
    re.IGNORECASE,
)
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")

_PUBLIC_ERROR_MESSAGES = {
    "authentication_required": "authentication required",
    "conflict": "request conflict",
    "guardrail_blocked": "guardrail blocked",
    "internal_error": "internal error",
    "not_found": "resource not found",
    "permission_denied": "permission denied",
    "pii_detected": "sensitive input detected",
    "prompt_injection_detected": "unsafe input detected",
    "tenant_required": "tenant required",
    "token_budget_exceeded": "token budget exceeded",
    "validation_failed": "request validation failed",
}
_PUBLIC_ERROR_MESSAGE_ALLOWLIST = {
    (
        "conflict",
        "reminder replay payload conflicts with the registered event",
    ),
    ("conflict", "workflow identity is immutable"),
}

_IDENTIFIER_KEYS = {
    "actorid",
    "agentid",
    "approvalid",
    "calendareventid",
    "causationid",
    "conversationid",
    "correlationid",
    "eventid",
    "fileid",
    "id",
    "idempotencykey",
    "jobid",
    "messageid",
    "parenteventid",
    "principalid",
    "promptid",
    "reminderid",
    "replyid",
    "requestid",
    "runid",
    "sourceeventid",
    "tenantid",
    "traceid",
    "userid",
    "workflowid",
}
_HASH_KEYS = {
    "digest",
    "fingerprint",
    "hash",
    "payloadfingerprint",
    "requesthash",
    "sha256",
}
_METRIC_KEYS = {
    "accepted",
    "confidence",
    "count",
    "durationms",
    "end",
    "identityversion",
    "inputtokens",
    "latencyms",
    "limit",
    "matched",
    "mediafilesize",
    "outputtokens",
    "sizebytes",
    "start",
    "temperature",
    "textlength",
    "threshold",
    "tokencount",
    "truncatedcount",
}
_CATEGORY_KEYS = {
    "audioformat",
    "category",
    "channel",
    "code",
    "component",
    "contenttype",
    "errortype",
    "extension",
    "field",
    "format",
    "kind",
    "label",
    "language",
    "mediakind",
    "mediamimetype",
    "model",
    "name",
    "operation",
    "promptversion",
    "provider",
    "retryable",
    "schema",
    "severity",
    "source",
    "stage",
    "status",
    "step",
    "telegramfileextension",
    "tier",
    "timezone",
    "type",
    "validation",
    "version",
    "workflow",
    "workflowtype",
}
_CONTAINER_KEYS = {
    "categories",
    "context",
    "details",
    "errors",
    "findings",
    "items",
    "loc",
    "metadata",
    "result",
    "results",
}
_SENSITIVE_KEY_PARTS = {
    "apikey",
    "authorization",
    "body",
    "content",
    "cookie",
    "credential",
    "document",
    "excerpt",
    "input",
    "message",
    "password",
    "passwd",
    "prompt",
    "query",
    "reason",
    "secret",
    "session",
    "text",
    "token",
    "transcript",
    "url",
    "uri",
}
_BINARY_KEY_PARTS = {
    "attachment",
    "audio",
    "binary",
    "blob",
    "bytes",
    "data",
    "file",
    "image",
    "media",
    "payload",
}


def redact_trace_mapping(value: object) -> dict[str, Any]:
    """Return allowlisted trace metadata with unsafe values removed or reduced."""

    if not isinstance(value, Mapping):
        return {}
    sanitized = _sanitize_mapping(value, depth=0, seen=set())
    return sanitized if isinstance(sanitized, dict) else {}


def redact_error_context(value: object) -> dict[str, Any]:
    """Apply the same fail-closed policy to structured error context."""

    return redact_trace_mapping(value)


def redact_error_message(value: object) -> str:
    """Redact credentials and URLs while retaining a controlled error summary."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"{REDACTED} binary size={len(value)}"
    text = str(value)
    text = _URL_RE.sub(REDACTED_URL, text)
    text = _AUTHORITY_CREDENTIAL_RE.sub(REDACTED_URL, text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text
    )
    text = _BEARER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _PRIVATE_TEXT_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}={REDACTED}", text
    )
    text = _JWT_RE.sub(REDACTED, text)
    text = _OPAQUE_SECRET_RE.sub(REDACTED, text)
    text = _TELEGRAM_TOKEN_RE.sub(REDACTED, text)
    return text[:500]


def public_error_message(code: object, candidate: object | None = None) -> str:
    """Return the explicit public message for one structured error category."""

    if isinstance(code, Enum):
        code = code.value
    code_text = str(code)
    candidate_text = (
        redact_error_message(candidate).strip() if candidate is not None else ""
    )
    if (code_text, candidate_text) in _PUBLIC_ERROR_MESSAGE_ALLOWLIST:
        return candidate_text
    return _PUBLIC_ERROR_MESSAGES.get(code_text, "request failed")


def redacted_text_metadata(value: object) -> dict[str, int | str]:
    """Return only the diagnostic length and digest of arbitrary text."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        content = bytes(value)
        return {"message_length": len(content), "message_sha256": _sha256(content)}
    text = str(value)
    return {
        "message_length": len(text),
        "message_sha256": _sha256(text.encode("utf-8")),
    }


def safe_identifier(value: object) -> str:
    """Keep a safe opaque identifier or replace an unsafe one with a stable hash."""

    text = str(value).strip()
    if not text:
        return text
    if _is_sensitive_string(text) or _SAFE_IDENTIFIER_RE.fullmatch(text) is None:
        return f"sha256:{_sha256(text.encode('utf-8'))}"
    return text


def safe_optional_identifier(value: object | None) -> str | None:
    """Optional form of :func:`safe_identifier`."""

    if value is None:
        return None
    return safe_identifier(value)


def safe_category(value: object | None) -> str | None:
    """Keep one bounded categorical value without accepting free-form text."""

    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    if not text:
        return text
    if _is_sensitive_string(text) or _SAFE_CATEGORY_RE.fullmatch(text) is None:
        return REDACTED
    return text


def safe_context_refs(value: object) -> list[str]:
    """Sanitize the bounded list of categorical trace context references."""

    if not isinstance(value, (list, tuple)):
        return []
    refs: list[str] = []
    for item in value[:_MAX_ITEMS]:
        sanitized = safe_category(item)
        if sanitized:
            refs.append(sanitized)
    return refs


def binary_metadata(value: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Reduce binary content to type, byte count, and a content hash."""

    content = bytes(value)
    return {
        "kind": "binary",
        "size_bytes": len(content),
        "sha256": _sha256(content),
    }


def _sanitize_mapping(
    value: Mapping[object, object], *, depth: int, seen: set[int]
) -> dict[str, Any] | str:
    if depth > _MAX_DEPTH or id(value) in seen:
        return REDACTED
    seen.add(id(value))
    output: dict[str, Any] = {}
    try:
        item_count = len(value)
        items = list(islice(value.items(), _MAX_ITEMS))
        for raw_key, raw_value in items:
            if not isinstance(raw_key, str):
                continue
            key = raw_key.strip()
            if (
                not key
                or len(key) > _MAX_KEY_LENGTH
                or _SAFE_KEY_RE.fullmatch(key) is None
            ):
                continue
            canonical = _canonical_key(key)

            if canonical in _METRIC_KEYS or _is_metric_key(canonical):
                metric = _sanitize_metric(raw_value)
                if metric is not None:
                    output[key] = metric
                else:
                    _store_redacted(output, key, raw_value)
                continue
            if canonical in _IDENTIFIER_KEYS:
                output[key] = _sanitize_identifier_value(raw_value)
                continue
            if canonical in _HASH_KEYS or canonical.endswith(
                ("hash", "fingerprint", "sha256")
            ):
                output[key] = _sanitize_hash(raw_value)
                continue
            if canonical in _CATEGORY_KEYS:
                output[key] = _sanitize_category_value(raw_value)
                continue
            if canonical in _CONTAINER_KEYS:
                output[key] = _sanitize_container(
                    raw_value, key=canonical, depth=depth + 1, seen=seen
                )
                continue
            if _is_sensitive_key(canonical):
                _store_redacted(output, key, raw_value)
                continue
            if any(part in canonical for part in _BINARY_KEY_PARTS):
                if isinstance(raw_value, Mapping):
                    metadata = _sanitize_mapping(raw_value, depth=depth + 1, seen=seen)
                    if _is_binary_metadata(metadata):
                        output[key] = metadata
                    else:
                        _store_redacted(output, key, raw_value)
                else:
                    _store_redacted(output, key, raw_value)
                continue
            # Unknown fields are deliberately omitted. New metadata must be
            # explicitly classified before it may cross this boundary.
        if item_count > _MAX_ITEMS:
            output["truncated_count"] = item_count - _MAX_ITEMS
        return output
    finally:
        seen.remove(id(value))


def _sanitize_container(value: object, *, key: str, depth: int, seen: set[int]) -> Any:
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth=depth, seen=seen)
    if not isinstance(value, (list, tuple)):
        return REDACTED
    if id(value) in seen:
        return REDACTED
    seen.add(id(value))
    try:
        output: list[Any] = []
        for item in value[:_MAX_ITEMS]:
            if isinstance(item, Mapping):
                output.append(_sanitize_mapping(item, depth=depth + 1, seen=seen))
            elif isinstance(item, (list, tuple)):
                output.append(
                    _sanitize_container(item, key=key, depth=depth + 1, seen=seen)
                )
            elif key == "loc":
                output.append(_sanitize_identifier_value(item))
            elif key == "categories":
                output.append(_sanitize_category_value(item))
            elif isinstance(item, (bytes, bytearray, memoryview)):
                output.append(binary_metadata(item))
            else:
                output.append(REDACTED)
        if len(value) > _MAX_ITEMS:
            output.append({"truncated_count": len(value) - _MAX_ITEMS})
        return output
    finally:
        seen.remove(id(value))


def _sanitize_identifier_value(value: object) -> Any:
    if isinstance(value, (list, tuple)):
        return [safe_identifier(item) for item in value[:_MAX_ITEMS]]
    return safe_identifier(value)


def _sanitize_hash(value: object) -> str:
    text = str(value).strip()
    if _SAFE_HASH_RE.fullmatch(text):
        return text
    return f"sha256:{_sha256(text.encode('utf-8'))}"


def _sanitize_metric(value: object) -> bool | int | float | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _sanitize_category_value(value: object) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_category_value(item) for item in value[:_MAX_ITEMS]]
    return safe_category(value)


def _store_redacted(output: dict[str, Any], key: str, value: object) -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        output[key] = binary_metadata(value)
        return
    if isinstance(value, str) and value in {REDACTED, REDACTED_URL}:
        output[key] = value
        return
    output[key] = (
        REDACTED_URL if isinstance(value, str) and _URL_RE.search(value) else REDACTED
    )
    if isinstance(value, str):
        output[f"{key}_length"] = len(value)
        output[f"{key}_sha256"] = _sha256(value.encode("utf-8"))
    elif isinstance(value, Mapping) or isinstance(value, (list, tuple)):
        output[f"{key}_count"] = len(value)


def _canonical_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _is_metric_key(key: str) -> bool:
    return key.endswith(
        (
            "bytes",
            "count",
            "durationms",
            "length",
            "latencyms",
            "size",
            "tokens",
        )
    )


def _is_sensitive_key(key: str) -> bool:
    return any(part in key for part in _SENSITIVE_KEY_PARTS)


def _is_binary_metadata(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("kind") == "binary"
        and isinstance(value.get("size_bytes"), int)
        and isinstance(value.get("sha256"), str)
        and _SAFE_HASH_RE.fullmatch(value["sha256"]) is not None
    )


def _is_sensitive_string(value: str) -> bool:
    return bool(
        _URL_RE.search(value)
        or _AUTHORITY_CREDENTIAL_RE.search(value)
        or _SECRET_ASSIGNMENT_RE.search(value)
        or _PRIVATE_TEXT_ASSIGNMENT_RE.search(value)
        or _BEARER_RE.search(value)
        or _JWT_RE.search(value)
        or _OPAQUE_SECRET_RE.search(value)
        or _TELEGRAM_TOKEN_RE.search(value)
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
