"""Telegram webhook router for the HTTP runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, Security

from personal_assistant.adapters.inbound.api import normalize_telegram_webhook
from personal_assistant.adapters.inbound.channels.telegram import (
    TelegramActorNotVerifiableError,
)
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.reminders.idempotency import ReminderIdempotencyConflict
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_auth import (
    TELEGRAM_WEBHOOK_SECRET_HEADER,
    _require_telegram_webhook_secret,
    telegram_principal,
)
from personal_assistant.infrastructure.http_dynamic import get_http_attribute
from personal_assistant.infrastructure.http_models import TelegramWebhookResponse
from personal_assistant.infrastructure.http_telegram_replies import (
    _send_telegram_audio_reply,
    _send_telegram_reply,
    _should_send_audio_reply,
)
from personal_assistant.infrastructure.http_telegram_transcription import (
    _transcribe_telegram_media,
)


def register_telegram_routes(
    app: FastAPI,
    container: AppContainer,
    settings: AppSettings,
) -> None:
    """Register the Telegram inbound webhook endpoint on the FastAPI application."""
    replies = container.commands.replies

    @app.post(
        "/webhooks/telegram",
        response_model=TelegramWebhookResponse,
        tags=["telegram"],
    )
    def telegram_webhook(
        payload: dict[str, Any],
        x_telegram_secret: Annotated[
            str | None,
            Security(TELEGRAM_WEBHOOK_SECRET_HEADER),
        ],
    ) -> TelegramWebhookResponse:
        _require_telegram_webhook_secret(settings, x_telegram_secret)

        normalizer = get_http_attribute(
            "normalize_telegram_webhook", normalize_telegram_webhook
        )
        try:
            message = normalizer(payload, tenant_id=settings.tenant_id)
        except TelegramActorNotVerifiableError as exc:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "telegram update has no verifiable actor",
                tenant_id=settings.tenant_id,
            ) from exc
        principal = telegram_principal(settings, message.actor_id)
        if message.media_kind in {"voice", "audio"}:
            transcribed, transcription_error = _transcribe_telegram_media(
                container,
                settings,
                message,
                replies,
            )
            if transcription_error is not None:
                sent = False
                if settings.telegram_bot_token:
                    sent = _send_telegram_reply(
                        container,
                        principal,
                        chat_id=message.conversation_id,
                        text=transcription_error,
                        idempotency_key=message.idempotency_key
                        or f"telegram:{message.conversation_id}:{message.message_id}",
                    )
                return TelegramWebhookResponse(
                    status=AgentStatus.needs_clarification,
                    reply=transcription_error,
                    sent=sent,
                    approval_id=None,
                    command=message.command,
                )
            if transcribed is not None:
                message = transcribed

        active_datetime = get_http_attribute("datetime", datetime)
        try:
            result = container.commands.handle(
                principal,
                message,
                now=active_datetime.now(UTC),
                timezone=settings.timezone,
            )
        except ReminderIdempotencyConflict:
            # Telegram must acknowledge provider delivery with HTTP 200 while
            # exposing no internal key/fingerprint metadata and performing no
            # reply, calendar, scheduler, event, or outbox side effect.
            return TelegramWebhookResponse(
                status=AgentStatus.failed,
                reply=replies.reminder_replay_conflict(),
                sent=False,
                audio_sent=False,
                approval_id=None,
                command=message.command,
            )
        sent = False
        audio_sent = False
        if settings.telegram_bot_token:
            sent = _send_telegram_reply(
                container,
                principal,
                chat_id=message.conversation_id,
                text=result.reply,
                idempotency_key=message.idempotency_key
                or f"telegram:{message.conversation_id}:{message.message_id}",
            )
            if sent and _should_send_audio_reply(settings, message, result.reply):
                audio_sent = _send_telegram_audio_reply(
                    container,
                    principal,
                    settings,
                    chat_id=message.conversation_id,
                    text=result.reply,
                    idempotency_key=message.idempotency_key
                    or f"telegram:{message.conversation_id}:{message.message_id}",
                )
        return TelegramWebhookResponse(
            status=result.status,
            reply=result.reply,
            sent=sent,
            audio_sent=audio_sent,
            approval_id=result.approval_id,
            command=message.command,
        )
