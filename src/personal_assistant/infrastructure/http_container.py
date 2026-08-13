"""Application container factory for the local HTTP runtime."""

from __future__ import annotations

from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
    TelegramNotificationTool,
)
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
    if settings.reminder_worker_enabled and not settings.telegram_bot_token:
        raise RuntimeError("REMINDER_WORKER_ENABLED requires TELEGRAM_BOT_TOKEN")
    prompts = build_prompt_catalog()
    log_egress_audit(settings)
    llm = build_llm_provider(settings, prompt_catalog=prompts)
    transcription = build_transcription_provider(settings)
    tts = build_tts_provider(settings)
    if settings.telegram_bot_token:
        telegram_notifications = TelegramNotificationTool(
            TelegramBotApiClient(
                token=settings.telegram_bot_token,
                egress_allowlist=build_egress_allowlist(settings),
            ),
        )
        return build_container(
            settings=settings,
            llm=llm,
            notifications=telegram_notifications,
            transcription=transcription,
            tts=tts,
            prompt_catalog=prompts,
            approve_reminder_notifications=True,
            reminder_minutes_before=settings.reminder_minutes_before,
            llm_context_window_tokens=settings.llm_context_window_tokens,
        )
    return build_container(
        settings=settings,
        llm=llm,
        transcription=transcription,
        tts=tts,
        prompt_catalog=prompts,
        reminder_minutes_before=settings.reminder_minutes_before,
        llm_context_window_tokens=settings.llm_context_window_tokens,
    )
