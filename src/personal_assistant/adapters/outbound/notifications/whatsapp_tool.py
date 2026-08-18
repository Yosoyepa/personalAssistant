"""WhatsApp notification tool implementing NotificationPort with P5 approvals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from personal_assistant.adapters.outbound.notifications.whatsapp_models import (
    WhatsAppClient,
    WhatsAppProviderResult,
)
from personal_assistant.application.ports.notifications import (
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


def _fingerprint(request: NotificationRequest) -> str:
    payload_data = request.model_dump(mode="python", exclude={"media": {"data"}})
    if request.media is not None:
        payload_data["media"]["sha256"] = hashlib.sha256(request.media.data).hexdigest()
    payload = json.dumps(
        payload_data, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_provider_result(value: object) -> WhatsAppProviderResult:
    if isinstance(value, WhatsAppProviderResult):
        return value
    if isinstance(value, Mapping):
        msg_id = value.get("notification_id") or value.get("message_id")
        if isinstance(msg_id, str) and msg_id.strip():
            return WhatsAppProviderResult(
                outcome="success", notification_id=msg_id.strip()
            )
    return WhatsAppProviderResult(outcome="unknown-outcome")


class WhatsAppNotificationTool:
    """P5 WhatsApp dispatcher. The agent never calls this adapter directly."""

    permission_tier = PermissionTier.P5

    def __init__(self, client: WhatsAppClient) -> None:
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
        if request.channel != "whatsapp":
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "WhatsAppNotificationTool only dispatches whatsapp notifications",
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
                "whatsapp notification idempotency conflict",
                tenant_id=principal.tenant_id,
            )
        existing = self._terminal_by_key.get(key)
        if existing is not None:
            return existing.model_copy(update={"reused": True})

        self._fingerprints[key] = request_fingerprint
        provider_result = _coerce_provider_result(
            self._client.send_message(recipient=request.recipient, text=request.body)
        )
        notification_id = None
        if provider_result.outcome == "success" and provider_result.notification_id:
            raw_id = provider_result.notification_id
            notification_id = (
                raw_id if raw_id.startswith("whatsapp:") else f"whatsapp:{raw_id}"
            )

        result = NotificationResult(
            notification_id=notification_id,
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            outcome=provider_result.outcome,
            provider_code=provider_result.provider_code,
            retry_after=provider_result.retry_after,
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
