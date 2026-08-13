"""Telegram notification reply dispatch and voice reply synthesis."""

from __future__ import annotations

from typing import Literal, cast

from personal_assistant.application.dto.channels import NormalizedMessage
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AudioSynthesisRequest
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.ports.notifications import (
    NotificationMedia,
    NotificationRequest,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.config import AppSettings


def _send_telegram_reply(
    container: AppContainer,
    principal: Principal,
    *,
    chat_id: str,
    text: str,
    idempotency_key: str,
) -> bool:
    """Send a text reply to Telegram via notifications port."""
    request = NotificationRequest(
        channel="telegram",
        recipient=chat_id,
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
        container.notifications.send(principal, request, approval=approval)
    except Exception:
        # Telegram already delivered the update; provider send failures should
        # not force Telegram to retry the webhook and duplicate workflow work.
        return False
    return True


def _should_send_audio_reply(
    settings: AppSettings, message: NormalizedMessage, text: str
) -> bool:
    """Check if audio reply should be sent based on settings and incoming message."""
    if settings.telegram_audio_reply_mode in {"", "disabled", "none"}:
        return False
    if len(text) > settings.tts_max_reply_characters:
        return False
    if settings.telegram_audio_reply_mode == "always":
        return True
    if settings.telegram_audio_reply_mode in {
        "voice_only",
        "voice-only",
        "audio_only",
        "audio-only",
    }:
        return message.media_kind in {"voice", "audio"}
    return False


def _trace_telegram_audio_reply_failure(
    container: AppContainer,
    principal: Principal,
    *,
    chat_id: str,
    text: str,
    idempotency_key: str,
    stage: str,
    tool_name: str,
    exc: Exception,
) -> None:
    """Emit trace event for audio reply failures."""
    container.traces.write(
        TraceEvent(
            run_id=f"telegram:{chat_id}:{idempotency_key}:audio-reply",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id=principal.tenant_id,
            input_summary={
                "operation": "telegram.audio_reply",
                "stage": stage,
                "text_length": len(text),
            },
            tool_call={
                "name": tool_name,
                "idempotency_key": f"{idempotency_key}:reply-audio",
            },
            error={
                "type": exc.__class__.__name__,
                "message": str(exc)[:500],
                "category": "audio",
            },
        )
    )


def _send_telegram_audio_reply(
    container: AppContainer,
    principal: Principal,
    settings: AppSettings,
    *,
    chat_id: str,
    text: str,
    idempotency_key: str,
) -> bool:
    """Synthesize voice reply and send audio message to Telegram."""
    if container.tts is None:
        return False
    if len(text) > settings.tts_max_reply_characters:
        return False
    try:
        synthesized = container.tts.synthesize(
            request=AudioSynthesisRequest(
                text=text,
                voice_id=settings.tts_voice_id,
                audio_format=cast(
                    Literal["mp3", "wav", "flac"], settings.tts_audio_format
                ),
                language_boost=settings.tts_language_boost,
            ),
            budget=TokenBudget(limit=settings.tts_max_reply_characters),
        )
    except Exception as exc:
        _trace_telegram_audio_reply_failure(
            container,
            principal,
            chat_id=chat_id,
            text=text,
            idempotency_key=idempotency_key,
            stage="synthesize",
            tool_name="audio.synthesize",
            exc=exc,
        )
        return False

    try:
        request = NotificationRequest(
            channel="telegram",
            recipient=chat_id,
            body=text,
            idempotency_key=f"{idempotency_key}:reply-audio",
            media=NotificationMedia(
                filename=f"assistant-reply.{synthesized.filename_extension}",
                content_type=synthesized.content_type,
                data=synthesized.audio,
            ),
        )
        approval = ApprovalGrant.issue(
            principal=principal,
            action="notification.send",
            resource=request.idempotency_key,
            tier=PermissionTier.P5,
            approval_id=f"{idempotency_key}:reply-audio",
        )
        container.notifications.send(principal, request, approval=approval)
    except Exception as exc:
        _trace_telegram_audio_reply_failure(
            container,
            principal,
            chat_id=chat_id,
            text=text,
            idempotency_key=idempotency_key,
            stage="send",
            tool_name="notification.send",
            exc=exc,
        )
        return False
    return True
