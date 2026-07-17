"""Telegram notification adapter with P5 approval and idempotency checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from http.client import HTTPException
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib import request as urllib_request
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_assistant.application.ports.notifications import (
    NotificationOutcome,
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionTier,
    require_approval,
)


class TelegramProviderResult(BaseModel):
    """Sanitized outcome returned by the Telegram transport boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: NotificationOutcome
    provider_code: int | None = Field(default=None, strict=True, ge=100, le=599)
    retry_after: int | None = Field(default=None, strict=True, gt=0)
    provider_message_id: int | None = Field(default=None, strict=True, gt=0)

    @model_validator(mode="after")
    def validate_metadata(self) -> TelegramProviderResult:
        if self.outcome == "success":
            if self.provider_message_id is None:
                raise ValueError("Telegram success requires a confirmed message id")
            if self.provider_code is not None or self.retry_after is not None:
                raise ValueError("Telegram success cannot carry failure metadata")
            return self
        if self.provider_message_id is not None:
            raise ValueError("Telegram failure cannot carry a confirmed message id")
        if self.retry_after is not None and self.outcome != "known-transient":
            raise ValueError("retry_after is only valid for known-transient outcomes")
        return self


class TelegramBotClient(Protocol):
    def send_message(self, *, chat_id: str, text: str) -> TelegramProviderResult:
        """Send one Telegram message and return provider metadata."""

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> TelegramProviderResult:
        """Send one Telegram audio file and return provider metadata."""


class TelegramBotApiClient:
    """Small stdlib Telegram Bot API client for infrastructure dispatch."""

    def __init__(self, *, token: str, timeout_seconds: float = 10.0) -> None:
        if not token.strip():
            raise ValueError("telegram bot token is required")
        self._token = token
        self._timeout_seconds = timeout_seconds

    def send_message(self, *, chat_id: str, text: str) -> TelegramProviderResult:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib_request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(req)

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> TelegramProviderResult:
        boundary = f"pa-{uuid4().hex}"
        body = bytearray()
        body.extend(_multipart_field(boundary, "chat_id", chat_id))
        body.extend(_multipart_field(boundary, "caption", caption))
        body.extend(_multipart_file(boundary, "audio", filename, content_type, data))
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        req = urllib_request.Request(
            f"https://api.telegram.org/bot{self._token}/sendAudio",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._send(req)

    def _send(self, req: urllib_request.Request) -> TelegramProviderResult:
        try:
            with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
                status = _positive_int(getattr(response, "status", None))
                headers = getattr(response, "headers", None)
        except HTTPError as exc:
            raw = _read_http_error_body(exc)
            return _classify_failure(
                provider_code=_http_status(exc.code),
                raw=raw,
                retry_after_header=_retry_after_header(exc.headers),
            )
        except (
            TimeoutError,
            ConnectionResetError,
            HTTPException,
            URLError,
            OSError,
        ):
            return TelegramProviderResult(outcome="unknown-outcome")

        if status is not None and status >= 400:
            return _classify_failure(
                provider_code=status,
                raw=raw,
                retry_after_header=_retry_after_header(headers),
            )
        return _classify_response(raw)

    def get_file(self, *, file_id: str) -> Mapping[str, Any]:
        payload = json.dumps({"file_id": file_id}).encode("utf-8")
        req = urllib_request.Request(
            f"https://api.telegram.org/bot{self._token}/getFile",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
            raw = response.read()
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            raise RuntimeError("Telegram getFile failed")
        result = decoded.get("result") or {}
        if not isinstance(result, Mapping):
            raise RuntimeError("Telegram getFile returned invalid result")
        return result

    def download_file(self, *, file_path: str) -> bytes:
        req = urllib_request.Request(
            f"https://api.telegram.org/file/bot{self._token}/{file_path}",
            method="GET",
        )
        with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
            return response.read()


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file(
    boundary: str, name: str, filename: str, content_type: str, data: bytes
) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    return header + data + b"\r\n"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    return None


def _http_status(value: object) -> int | None:
    status = _positive_int(value)
    return status if status is not None and 100 <= status <= 599 else None


def _retry_after_seconds(value: object) -> int | None:
    seconds = _positive_int(value)
    if seconds is None and isinstance(value, str):
        stripped = value.strip()
        if stripped.isascii() and stripped.isdecimal():
            seconds = int(stripped)
    return seconds if seconds is not None and seconds > 0 else None


def _retry_after_header(headers: object) -> int | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    return _retry_after_seconds(headers.get("Retry-After"))


def _read_http_error_body(exc: HTTPError) -> bytes:
    try:
        try:
            body = exc.read()
        except (HTTPException, OSError, ValueError):
            return b""
        return body if isinstance(body, bytes) else b""
    finally:
        try:
            exc.close()
        except Exception:
            pass


def _decode_payload(raw: bytes) -> Mapping[str, Any] | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _payload_error_code(payload: Mapping[str, Any] | None) -> int | None:
    if payload is None:
        return None
    return _http_status(payload.get("error_code"))


def _payload_retry_after(payload: Mapping[str, Any] | None) -> int | None:
    if payload is None:
        return None
    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    return _retry_after_seconds(parameters.get("retry_after"))


def _failure_outcome(provider_code: int | None) -> NotificationOutcome:
    if provider_code == 429 or (provider_code is not None and provider_code >= 500):
        return "known-transient"
    if provider_code is not None and 400 <= provider_code < 500:
        return "permanent"
    return "unknown-outcome"


def _classify_failure(
    *,
    provider_code: int | None,
    raw: bytes,
    retry_after_header: int | None = None,
) -> TelegramProviderResult:
    payload = _decode_payload(raw)
    code = provider_code or _payload_error_code(payload)
    outcome = _failure_outcome(code)
    retry_candidates = (
        _payload_retry_after(payload),
        retry_after_header,
    )
    retry_after = max(
        (item for item in retry_candidates if item is not None), default=None
    )
    return TelegramProviderResult(
        outcome=outcome,
        provider_code=code,
        retry_after=retry_after if outcome == "known-transient" else None,
    )


def _classify_response(raw: bytes) -> TelegramProviderResult:
    payload = _decode_payload(raw)
    if payload is None:
        return TelegramProviderResult(outcome="unknown-outcome")
    if payload.get("ok") is not True:
        return _classify_failure(provider_code=None, raw=raw)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return TelegramProviderResult(outcome="unknown-outcome")
    message_id = _positive_int(result.get("message_id"))
    if message_id is None:
        return TelegramProviderResult(outcome="unknown-outcome")
    return TelegramProviderResult(
        outcome="success",
        provider_message_id=message_id,
    )


def _fingerprint(request: NotificationRequest) -> str:
    payload_data = request.model_dump(mode="python", exclude={"media": {"data"}})
    if request.media is not None:
        payload_data["media"]["sha256"] = hashlib.sha256(request.media.data).hexdigest()
    payload = json.dumps(
        payload_data, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TelegramNotificationTool:
    """P5 Telegram dispatcher. The agent never calls this adapter directly."""

    permission_tier = PermissionTier.P5

    def __init__(self, client: TelegramBotClient) -> None:
        self._client = client
        self._terminal_by_key: dict[tuple[str, str], NotificationResult] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        if request.channel != "telegram":
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "TelegramNotificationTool only dispatches telegram notifications",
                tenant_id=principal.tenant_id,
            )
        require_approval(
            principal=principal,
            tier=self.permission_tier,
            approval=approval,
            action="notification.send",
            resource=request.idempotency_key,
        )
        key = (principal.tenant_id, request.idempotency_key)
        request_fingerprint = _fingerprint(request)
        stored_fingerprint = self._fingerprints.get(key)
        if stored_fingerprint is not None and stored_fingerprint != request_fingerprint:
            raise AssistantError(
                ErrorCode.CONFLICT,
                "telegram notification idempotency conflict",
                tenant_id=principal.tenant_id,
            )
        existing = self._terminal_by_key.get(key)
        if existing is not None:
            return existing.model_copy(update={"reused": True})

        self._fingerprints[key] = request_fingerprint
        if request.media is None:
            provider_result = self._client.send_message(
                chat_id=request.recipient, text=request.body
            )
        elif request.media.kind == "audio":
            provider_result = self._client.send_audio(
                chat_id=request.recipient,
                caption=request.body,
                filename=request.media.filename,
                content_type=request.media.content_type,
                data=request.media.data,
            )
        else:
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "unsupported telegram notification media",
                tenant_id=principal.tenant_id,
            )
        provider_result = _coerce_provider_result(provider_result)
        provider_message_id = provider_result.provider_message_id
        result = NotificationResult(
            notification_id=(
                f"telegram:{provider_message_id}"
                if provider_message_id is not None
                else None
            ),
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            outcome=provider_result.outcome,
            provider_code=provider_result.provider_code,
            retry_after=provider_result.retry_after,
            provider_message_id=provider_message_id,
        )
        if result.outcome != "known-transient":
            self._terminal_by_key[key] = result
        return result

    def list_sent(self, principal: Principal) -> list[NotificationResult]:
        require_trusted_principal(principal)
        return [
            item.model_copy()
            for (tenant_id, _), item in self._terminal_by_key.items()
            if tenant_id == principal.tenant_id and item.outcome == "success"
        ]


def _coerce_provider_result(value: object) -> TelegramProviderResult:
    """Keep compatibility with simple successful Telegram client fakes."""

    if isinstance(value, TelegramProviderResult):
        return value
    if isinstance(value, Mapping):
        message_id = _positive_int(value.get("message_id"))
        if message_id is not None:
            return TelegramProviderResult(
                outcome="success",
                provider_message_id=message_id,
            )
    return TelegramProviderResult(outcome="unknown-outcome")
