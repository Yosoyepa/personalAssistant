"""Authentication and authorization utilities for the HTTP runtime."""

from __future__ import annotations

import secrets
from typing import cast

from fastapi import Request
from fastapi.security import APIKeyHeader

from personal_assistant.adapters.inbound.auth import (
    LocalPrincipalProvider,
    principal_from_auth_claims,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_dynamic import get_http_attribute

MAX_TELEGRAM_AUDIO_BYTES = 20 * 1024 * 1024
MAX_WHATSAPP_AUDIO_BYTES = 20 * 1024 * 1024
TELEGRAM_WEBHOOK_SECRET_HEADER = APIKeyHeader(
    name="X-Telegram-Bot-Api-Secret-Token",
    scheme_name="TelegramWebhookSecret",
    auto_error=False,
)
SUPPORTED_TRANSCRIPTION_EXTENSIONS = frozenset(
    {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "opus", "wav", "webm"}
)


def current_principal(request: Request) -> Principal:
    """Authenticate one server-owned principal for local HTTP surfaces."""

    provider = cast(
        LocalPrincipalProvider | None,
        getattr(request.app.state, "local_principal_provider", None),
    )
    if provider is None:
        raise AssistantError(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "valid local bearer credentials are required",
        )
    peer_host = request.client.host if request.client is not None else None
    return provider.authenticate(
        peer_host=peer_host,
        headers=request.headers,
        cookies=request.cookies,
    )


def telegram_principal(settings: AppSettings, actor_id: str) -> Principal:
    """Authenticate a Telegram webhook principal based on allowed user IDs."""
    if not actor_id or actor_id not in settings.telegram_allowed_user_ids:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "telegram user is not allowed",
            tenant_id=settings.tenant_id,
        )
    return principal_from_auth_claims(
        {"sub": actor_id, "tenant_id": settings.tenant_id},
        auth_provider="telegram",
        permission_tier=PermissionTier.P5,
    )


def _require_telegram_webhook_secret(
    settings: AppSettings, supplied_secret: str | None
) -> None:
    """Validate that the supplied secret matches the configured Telegram secret."""
    expected_secret = settings.telegram_webhook_secret
    candidate_secret = supplied_secret or ""
    active_secrets = get_http_attribute("secrets", secrets)
    matches = active_secrets.compare_digest(
        candidate_secret.encode("utf-8"), expected_secret.encode("utf-8")
    )
    if not expected_secret or not supplied_secret or not matches:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "telegram webhook authentication failed",
            tenant_id=settings.tenant_id,
        )
