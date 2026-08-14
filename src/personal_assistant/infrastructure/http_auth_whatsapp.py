"""WhatsApp webhook HMAC signature verification and principal resolution."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING

from personal_assistant.adapters.inbound.auth import principal_from_auth_claims
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier

if TYPE_CHECKING:
    from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings


def verify_whatsapp_signature(
    settings: WhatsAppSettings,
    body: bytes,
    signature_header: str | None,
) -> bool:
    """Verify HMAC-SHA256 signature against Meta's X-Hub-Signature-256 header."""
    if not signature_header or not settings.app_secret:
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    expected_sig = signature_header[len(prefix) :].strip()
    computed_sig = hmac.new(
        key=settings.app_secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return secrets.compare_digest(computed_sig, expected_sig)


def whatsapp_principal(
    settings: WhatsAppSettings,
    actor_id: str,
    *,
    tenant_id: str = "personal",
) -> Principal:
    """Derive authenticated Principal from authorized WhatsApp user ID."""
    clean_actor = actor_id.strip()
    if not clean_actor or clean_actor not in settings.allowed_user_ids:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "whatsapp sender is not allowed",
            tenant_id=tenant_id,
        )

    return principal_from_auth_claims(
        {"sub": clean_actor, "tenant_id": tenant_id},
        auth_provider="whatsapp",
        permission_tier=PermissionTier.P5,
    )
