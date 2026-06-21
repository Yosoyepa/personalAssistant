"""Local notification adapter with approval and idempotency checks."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from personal_assistant.shared.permissions import ApprovalGrant, PermissionTier, require_approval
from personal_assistant.shared.schemas import Principal


class NotificationRequest(BaseModel):
    channel: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1)
    send_at: datetime | None = None
    idempotency_key: str = Field(min_length=1)


class NotificationResult(BaseModel):
    notification_id: str
    channel: str
    recipient: str
    idempotency_key: str
    reused: bool = False


class LocalNotificationTool:
    """P5 communication tool. It never sends without approval."""

    permission_tier = PermissionTier.P5

    def __init__(self) -> None:
        self._sent_by_key: dict[tuple[str, str], NotificationResult] = {}

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        key = (principal.tenant_id, request.idempotency_key)
        existing = self._sent_by_key.get(key)
        if existing is not None:
            return existing.model_copy(update={"reused": True})

        require_approval(
            principal=principal,
            tier=self.permission_tier,
            approval=approval,
            action="notification.send",
            resource=request.idempotency_key,
        )
        result = NotificationResult(
            notification_id=f"msg_{uuid4().hex}",
            channel=request.channel,
            recipient=request.recipient,
            idempotency_key=request.idempotency_key,
        )
        self._sent_by_key[key] = result
        return result

    def list_sent(self, principal: Principal) -> list[NotificationResult]:
        return [item for (tenant_id, _), item in self._sent_by_key.items() if tenant_id == principal.tenant_id]
