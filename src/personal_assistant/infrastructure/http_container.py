"""Application container factory for the local HTTP runtime."""

from __future__ import annotations

from personal_assistant.adapters.outbound.notifications.router import (
    ChannelNotificationRouter,
)
from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
    TelegramNotificationTool,
)
from personal_assistant.adapters.outbound.notifications.whatsapp import (
    WhatsAppGraphApiClient,
    WhatsAppNotificationTool,
)
from personal_assistant.application.ports.notifications import NotificationPort
from personal_assistant.infrastructure.bootstrap import (
    AppContainer,
    build_container,
    build_egress_allowlist,
    build_llm_provider,
    build_transcription_provider,
    build_tts_provider,
    log_egress_audit,
)
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.prompts import build_prompt_catalog


def build_runtime_container(settings: AppSettings) -> AppContainer:
    """Instantiate and wire domain services and adapters according to runtime settings."""
    if settings.reminder_worker_enabled and settings.persistence_backend != "postgres":
        raise RuntimeError(
            "REMINDER_WORKER_ENABLED requires PERSISTENCE_BACKEND=postgres"
        )
    has_provider = bool(
        settings.telegram_bot_token
        or (settings.whatsapp.access_token and settings.whatsapp.phone_number_id)
    )
    if settings.reminder_worker_enabled and not has_provider:
        raise RuntimeError("REMINDER_WORKER_ENABLED requires TELEGRAM_BOT_TOKEN")
    prompts = build_prompt_catalog()
    log_egress_audit(settings)
    llm = build_llm_provider(settings, prompt_catalog=prompts)
    transcription = build_transcription_provider(settings)
    tts = build_tts_provider(settings)
    egress = build_egress_allowlist(settings)
    channel_tools: dict[str, NotificationPort] = {}
    if settings.telegram_bot_token:
        channel_tools["telegram"] = TelegramNotificationTool(
            TelegramBotApiClient(
                token=settings.telegram_bot_token,
                egress_allowlist=egress,
            ),
        )
    if settings.whatsapp.access_token and settings.whatsapp.phone_number_id:
        channel_tools["whatsapp"] = WhatsAppNotificationTool(
            WhatsAppGraphApiClient(
                access_token=settings.whatsapp.access_token,
                phone_number_id=settings.whatsapp.phone_number_id,
                egress_allowlist=egress,
            )
        )
    notifications = ChannelNotificationRouter(channel_tools) if channel_tools else None
    return build_container(
        settings=settings,
        llm=llm,
        notifications=notifications,
        transcription=transcription,
        tts=tts,
        prompt_catalog=prompts,
        approve_reminder_notifications=bool(notifications),
        reminder_minutes_before=settings.reminder_minutes_before,
        llm_context_window_tokens=settings.llm_context_window_tokens,
    )
