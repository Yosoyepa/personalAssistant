"""Local notification adapter with approval and idempotency checks."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionTier,
    require_approval,
)
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)


def _fingerprint(request: NotificationRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LocalNotificationTool:
    """P5 communication tool. It never sends without approval."""

    permission_tier = PermissionTier.P5

    def __init__(self) -> None:
        self._sent_by_key: dict[tuple[str, str], NotificationResult] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
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
                    "notification idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return existing.model_copy(update={"reused": True})
        result = NotificationResult(
            notification_id=f"msg_{uuid4().hex}",
            channel=request.channel,
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
