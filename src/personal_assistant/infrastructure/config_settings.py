"""Application settings model and validation."""

from __future__ import annotations

from dataclasses import dataclass, field

from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.config_constants import (
    DEFAULT_DATABASE_SCHEMA,
    DEFAULT_LLM_CONTEXT_WINDOW_TOKENS,
    DEFAULT_TRACE_RETENTION_DAYS,
)
from personal_assistant.infrastructure.config_validation import validate_app_settings
from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings


@dataclass(frozen=True, slots=True)
class AppSettings:
    tenant_id: str = field(default="personal", repr=False)
    timezone: str = "America/Bogota"
    reply_locale: str = "es"
    persistence_backend: str = "memory"
    database_url: str | None = field(default=None, repr=False)
    database_schema: str = DEFAULT_DATABASE_SCHEMA
    telegram_webhook_secret: str = field(default="", repr=False)
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_allowed_user_ids: frozenset[str] = field(default=frozenset(), repr=False)
    whatsapp: WhatsAppSettings = field(default_factory=WhatsAppSettings)
    llm_provider: str = "disabled"
    llm_api_key: str | None = field(default=None, repr=False)
    llm_base_url: str | None = field(default=None, repr=False)
    llm_model: str | None = None
    llm_auth_header: str = "x-api-key"
    llm_anthropic_version: str = "2023-06-01"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 512
    llm_context_window_tokens: int = DEFAULT_LLM_CONTEXT_WINDOW_TOKENS
    transcription_provider: str = "disabled"
    transcription_api_key: str | None = field(default=None, repr=False)
    transcription_base_url: str | None = field(default=None, repr=False)
    transcription_model: str | None = None
    transcription_timeout_seconds: float = 60.0
    tts_provider: str = "disabled"
    tts_api_key: str | None = field(default=None, repr=False)
    tts_base_url: str | None = field(default=None, repr=False)
    tts_model: str | None = None
    tts_voice_id: str = "male-qn-qingse"
    tts_audio_format: str = "mp3"
    tts_language_boost: str | None = "Spanish"
    tts_timeout_seconds: float = 30.0
    tts_max_reply_characters: int = 280
    telegram_audio_reply_mode: str = "disabled"
    admin_token: str | None = field(default=None, repr=False)
    local_auth_principal_id: str = field(default="local-user", repr=False)
    local_auth_permission_tier: PermissionTier = PermissionTier.P5
    public_base_url: str | None = field(default=None, repr=False)
    reminder_worker_enabled: bool = False
    reminder_worker_interval_seconds: float = 15.0
    reminder_worker_heartbeat_timeout_seconds: float = 45.0
    reminder_minutes_before: int = 30
    trace_retention_days: int = DEFAULT_TRACE_RETENTION_DAYS
    egress_allowed_hosts: frozenset[str] = field(default=frozenset())

    def __post_init__(self) -> None:
        validate_app_settings(self)

    @classmethod
    def from_env(cls) -> AppSettings:
        import importlib

        loader = importlib.import_module(
            "personal_assistant.infrastructure.config_loader"
        )
        return loader.load_app_settings_from_env(cls)
