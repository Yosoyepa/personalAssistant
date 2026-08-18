"""Response and error parsing helpers for WhatsApp Cloud API outbound requests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import suppress
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError

from personal_assistant.adapters.outbound.notifications.whatsapp_models import (
    WhatsAppProviderResult,
)
from personal_assistant.application.ports.notifications import (
    NotificationOutcome,
)


def _retry_after_header(headers: object) -> int | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    val = headers.get("Retry-After")
    if isinstance(val, int) and val > 0:
        return val
    if isinstance(val, str) and val.strip().isdecimal():
        parsed = int(val.strip())
        return parsed if parsed > 0 else None
    return None


def _read_http_error_body(exc: HTTPError) -> bytes:
    try:
        try:
            body = exc.read()
        except (HTTPException, OSError, ValueError):
            return b""
        return body if isinstance(body, bytes) else b""
    finally:
        with suppress(Exception):
            exc.close()


def _decode_payload(raw: bytes) -> Mapping[str, Any] | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _failure_outcome(code: int | None) -> NotificationOutcome:
    if code == 429 or (code is not None and code >= 500):
        return "known-transient"
    if code is not None and 400 <= code < 500:
        return "permanent"
    return "unknown-outcome"


def _classify_failure(
    *,
    provider_code: int | None,
    raw: bytes,
    retry_after_header: int | None = None,
) -> WhatsAppProviderResult:
    payload = _decode_payload(raw)
    code = provider_code
    if code is None and payload is not None:
        err = payload.get("error")
        if isinstance(err, Mapping) and isinstance(err.get("code"), int):
            code = err["code"]
    outcome = _failure_outcome(code)
    return WhatsAppProviderResult(
        outcome=outcome,
        provider_code=code,
        retry_after=retry_after_header if outcome == "known-transient" else None,
    )


def _classify_response(raw: bytes) -> WhatsAppProviderResult:
    payload = _decode_payload(raw)
    if payload is None:
        return WhatsAppProviderResult(outcome="unknown-outcome")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return WhatsAppProviderResult(outcome="unknown-outcome")
    first = messages[0]
    if not isinstance(first, Mapping):
        return WhatsAppProviderResult(outcome="unknown-outcome")
    message_id = first.get("id")
    if not isinstance(message_id, str) or not message_id.strip():
        return WhatsAppProviderResult(outcome="unknown-outcome")
    return WhatsAppProviderResult(
        outcome="success",
        notification_id=message_id.strip(),
    )
