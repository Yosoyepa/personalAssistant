"""Runtime configuration for local API, Telegram, and admin surfaces."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


@dataclass(frozen=True, slots=True)
class AppSettings:
    tenant_id: str = "personal"
    timezone: str = "America/Bogota"
    telegram_webhook_secret: str = "local-dev-secret"
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: frozenset[str] = frozenset()
    llm_provider: str = "disabled"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_auth_header: str = "x-api-key"
    llm_anthropic_version: str = "2023-06-01"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 512
    transcription_provider: str = "disabled"
    transcription_api_key: str | None = None
    transcription_base_url: str | None = None
    transcription_model: str | None = None
    transcription_timeout_seconds: float = 60.0
    admin_token: str | None = None
    public_base_url: str | None = None
    reminder_worker_interval_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "AppSettings":
        interval = os.getenv("REMINDER_WORKER_INTERVAL_SECONDS", "15")
        llm_timeout = os.getenv("LLM_TIMEOUT_SECONDS", "30")
        llm_max_tokens = os.getenv("LLM_MAX_TOKENS", "512")
        transcription_timeout = os.getenv("TRANSCRIPTION_TIMEOUT_SECONDS", "60")
        return cls(
            tenant_id=os.getenv("ASSISTANT_TENANT_ID", "personal").strip() or "personal",
            timezone=os.getenv("ASSISTANT_TIMEZONE", "America/Bogota").strip() or "America/Bogota",
            telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", "local-dev-secret").strip()
            or "local-dev-secret",
            telegram_bot_token=_optional_env("TELEGRAM_BOT_TOKEN"),
            telegram_allowed_user_ids=_parse_csv(os.getenv("TELEGRAM_ALLOWED_USER_IDS")),
            llm_provider=os.getenv("LLM_PROVIDER", "disabled").strip().lower() or "disabled",
            llm_api_key=_optional_env("LLM_API_KEY") or _optional_env("AEROLINK_API_KEY") or _optional_env("ANTHROPIC_API_KEY"),
            llm_base_url=_optional_env("LLM_BASE_URL") or _optional_env("AEROLINK_BASE_URL"),
            llm_model=_optional_env("LLM_MODEL") or _optional_env("AEROLINK_MODEL"),
            llm_auth_header=os.getenv("LLM_AUTH_HEADER", "x-api-key").strip() or "x-api-key",
            llm_anthropic_version=os.getenv("LLM_ANTHROPIC_VERSION", "2023-06-01").strip() or "2023-06-01",
            llm_timeout_seconds=max(float(llm_timeout), 1.0),
            llm_max_tokens=max(int(llm_max_tokens), 1),
            transcription_provider=os.getenv("TRANSCRIPTION_PROVIDER", "disabled").strip().lower() or "disabled",
            transcription_api_key=_optional_env("TRANSCRIPTION_API_KEY") or _optional_env("AEROLINK_API_KEY"),
            transcription_base_url=_optional_env("TRANSCRIPTION_BASE_URL") or _optional_env("AEROLINK_BASE_URL"),
            transcription_model=_optional_env("TRANSCRIPTION_MODEL"),
            transcription_timeout_seconds=max(float(transcription_timeout), 1.0),
            admin_token=_optional_env("ADMIN_TOKEN"),
            public_base_url=_optional_env("PUBLIC_BASE_URL"),
            reminder_worker_interval_seconds=max(float(interval), 1.0),
        )


def _parse_csv(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())
