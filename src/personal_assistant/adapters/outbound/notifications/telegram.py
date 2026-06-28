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


def _fingerprint(request: NotificationRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
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

        provider_result = self._client.send_message(chat_id=request.recipient, text=request.body)
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
