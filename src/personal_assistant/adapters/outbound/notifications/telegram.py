"""Telegram notification adapter with P5 approval and idempotency checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol
from urllib import request as urllib_request
from uuid import uuid4

from personal_assistant.application.ports.notifications import NotificationRequest, NotificationResult
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal, require_trusted_principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier, require_approval


class TelegramBotClient(Protocol):
    def send_message(self, *, chat_id: str, text: str) -> Mapping[str, Any]:
        """Send one Telegram message and return provider metadata."""

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> Mapping[str, Any]:
        """Send one Telegram audio file and return provider metadata."""


class TelegramBotApiClient:
    """Small stdlib Telegram Bot API client for infrastructure dispatch."""

    def __init__(self, *, token: str, timeout_seconds: float = 10.0) -> None:
        if not token.strip():
            raise ValueError("telegram bot token is required")
        self._token = token
        self._timeout_seconds = timeout_seconds

    def send_message(self, *, chat_id: str, text: str) -> Mapping[str, Any]:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib_request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
            raw = response.read()
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            raise RuntimeError("Telegram sendMessage failed")
        result = decoded.get("result") or {}
        if not isinstance(result, Mapping):
            return {}
        return result

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> Mapping[str, Any]:
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
        with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
            raw = response.read()
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            raise RuntimeError("Telegram sendAudio failed")
        result = decoded.get("result") or {}
        if not isinstance(result, Mapping):
            return {}
        return result

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


def _multipart_file(boundary: str, name: str, filename: str, content_type: str, data: bytes) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    return header + data + b"\r\n"


def _fingerprint(request: NotificationRequest) -> str:
    payload_data = request.model_dump(mode="python", exclude={"media": {"data"}})
    if request.media is not None:
        payload_data["media"]["sha256"] = hashlib.sha256(request.media.data).hexdigest()
    payload = json.dumps(payload_data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TelegramNotificationTool:
    """P5 Telegram dispatcher. The agent never calls this adapter directly."""

    permission_tier = PermissionTier.P5

    def __init__(self, client: TelegramBotClient) -> None:
        self._client = client
        self._sent_by_key: dict[tuple[str, str], NotificationResult] = {}
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
        existing = self._sent_by_key.get(key)
        if existing is not None:
            if self._fingerprints[key] != request_fingerprint:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "telegram notification idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return existing.model_copy(update={"reused": True})

        if request.media is None:
            provider_result = self._client.send_message(chat_id=request.recipient, text=request.body)
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
        provider_message_id = provider_result.get("message_id") if isinstance(provider_result, Mapping) else None
        result = NotificationResult(
            notification_id=f"telegram:{provider_message_id or uuid4().hex}",
            channel=request.channel,
            recipient=request.recipient,
            idempotency_key=request.idempotency_key,
        )
        self._sent_by_key[key] = result
        self._fingerprints[key] = request_fingerprint
        return result

    def list_sent(self, principal: Principal) -> list[NotificationResult]:
        require_trusted_principal(principal)
        return [
            item.model_copy()
            for (tenant_id, _), item in self._sent_by_key.items()
            if tenant_id == principal.tenant_id
        ]
