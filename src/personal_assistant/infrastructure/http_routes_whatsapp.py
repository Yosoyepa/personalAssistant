"""WhatsApp Cloud API inbound webhook router for the HTTP runtime."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse

from personal_assistant.adapters.inbound.api import normalize_whatsapp_webhook
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.reminders.idempotency import ReminderIdempotencyConflict
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_auth_whatsapp import (
    verify_whatsapp_signature,
    whatsapp_principal,
)
from personal_assistant.infrastructure.http_dynamic import get_http_attribute
from personal_assistant.infrastructure.http_models_whatsapp import (
    WhatsAppWebhookResponse,
)
from personal_assistant.infrastructure.http_whatsapp_replies import (
    _send_whatsapp_reply,
)
from personal_assistant.infrastructure.http_whatsapp_transcription import (
    _transcribe_whatsapp_media,
)


def _is_status_only_or_empty_callback(payload: dict[str, Any]) -> bool:
    entries = payload.get("entry") or []
    for entry in entries:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if value.get("messages"):
                return False
    return True


def register_whatsapp_routes(
    app: FastAPI,
    container: AppContainer,
    settings: AppSettings,
) -> None:
    """Register the WhatsApp inbound webhook routes on the FastAPI application."""

    @app.get(
        "/webhooks/whatsapp",
        tags=["whatsapp"],
    )
    def whatsapp_verify_webhook(
        hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
        hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
        hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
    ) -> PlainTextResponse:
        if (
            hub_mode == "subscribe"
            and hub_verify_token
            and settings.whatsapp.verify_token
            and secrets.compare_digest(hub_verify_token, settings.whatsapp.verify_token)
        ):
            return PlainTextResponse(content=hub_challenge or "")

        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "verification token mismatch",
            tenant_id=settings.tenant_id,
        )

    @app.post(
        "/webhooks/whatsapp",
        response_model=WhatsAppWebhookResponse,
        tags=["whatsapp"],
    )
    async def whatsapp_webhook(
        request: Request,
    ) -> WhatsAppWebhookResponse:
        if not settings.whatsapp.enabled:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "whatsapp channel is disabled",
                tenant_id=settings.tenant_id,
            )

        raw_body = await request.body()
        signature_header = request.headers.get(
            "x-hub-signature-256"
        ) or request.headers.get("X-Hub-Signature-256")
        if not verify_whatsapp_signature(settings.whatsapp, raw_body, signature_header):
            raise AssistantError(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "invalid signature",
                tenant_id=settings.tenant_id,
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "invalid json payload",
                tenant_id=settings.tenant_id,
            ) from exc

        if not isinstance(payload, dict) or _is_status_only_or_empty_callback(payload):
            return WhatsAppWebhookResponse(
                status=AgentStatus.completed,
                reply=None,
                sent=False,
                approval_id=None,
                command=None,
            )

        normalizer = get_http_attribute(
            "normalize_whatsapp_webhook", normalize_whatsapp_webhook
        )
        message = normalizer(payload, tenant_id=settings.tenant_id)
        if not message.actor_id or not message.text:
            return WhatsAppWebhookResponse(
                status=AgentStatus.completed,
                reply=None,
                sent=False,
                approval_id=None,
                command=None,
            )

        principal = whatsapp_principal(
            settings.whatsapp,
            message.actor_id,
            tenant_id=settings.tenant_id,
        )
        replies = container.commands.replies
        reply_sender = get_http_attribute("_send_whatsapp_reply", _send_whatsapp_reply)
        if message.media_kind in {"voice", "audio"}:
            transcribe_func = get_http_attribute(
                "_transcribe_whatsapp_media", _transcribe_whatsapp_media
            )
            transcribed, transcription_error = transcribe_func(
                container,
                settings,
                message,
                replies,
            )
            if transcription_error is not None:
                sent = False
                if settings.whatsapp.access_token:
                    sent = reply_sender(
                        container,
                        principal,
                        settings.whatsapp,
                        recipient=message.actor_id,
                        text=transcription_error,
                        idempotency_key=message.idempotency_key
                        or f"whatsapp:{message.actor_id}:{message.message_id}",
                    )
                return WhatsAppWebhookResponse(
                    status=AgentStatus.needs_clarification,
                    reply=transcription_error,
                    sent=sent,
                    approval_id=None,
                    command=message.command,
                )
            if transcribed is not None:
                message = transcribed
        elif message.media_kind in {"image", "document", "video"}:
            unsupported_reply = replies.whatsapp_media_unsupported()
            sent = False
            if settings.whatsapp.access_token:
                sent = reply_sender(
                    container,
                    principal,
                    settings.whatsapp,
                    recipient=message.actor_id,
                    text=unsupported_reply,
                    idempotency_key=message.idempotency_key
                    or f"whatsapp:{message.actor_id}:{message.message_id}",
                )
            return WhatsAppWebhookResponse(
                status=AgentStatus.needs_clarification,
                reply=unsupported_reply,
                sent=sent,
                approval_id=None,
                command=message.command,
            )

        active_datetime = get_http_attribute("datetime", datetime)
        try:
            result = container.commands.handle(
                principal,
                message,
                now=active_datetime.now(UTC),
                timezone=settings.timezone,
            )
        except ReminderIdempotencyConflict:
            return WhatsAppWebhookResponse(
                status=AgentStatus.failed,
                reply=container.commands.replies.reminder_replay_conflict(),
                sent=False,
                approval_id=None,
                command=message.command,
            )

        sent = False
        if settings.whatsapp.access_token and result.reply:
            sent = reply_sender(
                container,
                principal,
                settings.whatsapp,
                recipient=message.actor_id,
                text=result.reply,
                idempotency_key=message.idempotency_key
                or f"whatsapp:{message.actor_id}:{message.message_id}",
            )

        return WhatsAppWebhookResponse(
            status=result.status,
            reply=result.reply,
            sent=sent,
            approval_id=result.approval_id,
            command=message.command,
        )
