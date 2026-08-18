"""WhatsApp notification reply dispatch helper for HTTP webhooks."""

from __future__ import annotations

from personal_assistant.application.ports.notifications import (
    NotificationRequest,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings


def _send_whatsapp_reply(
    container: AppContainer,
    principal: Principal,
    settings: WhatsAppSettings,
    *,
    recipient: str,
    text: str,
    idempotency_key: str,
) -> bool:
    """Send a text reply to WhatsApp via notifications port."""
    if not settings.access_token or not text.strip():
        return False
    if container.notifications is None:
        return False
    request = NotificationRequest(
        channel="whatsapp",
        recipient=recipient,
        body=text,
        idempotency_key=f"{idempotency_key}:reply",
    )
    approval = ApprovalGrant.issue(
        principal=principal,
        action="notification.send",
        resource=request.idempotency_key,
        tier=PermissionTier.P5,
        approval_id=f"{idempotency_key}:reply",
    )
    try:
        result = container.notifications.send(principal, request, approval=approval)
        return result.outcome == "success"
    except Exception:
        # WhatsApp already delivered the webhook; provider send failures should
        # not force WhatsApp to retry the webhook and duplicate workflow work.
        return False
