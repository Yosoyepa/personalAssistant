"""Assembly loader for AppSettings from environment variables and dotenv."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.config_constants import (
    DEFAULT_DATABASE_SCHEMA,
    DEFAULT_TRACE_RETENTION_DAYS,
)
from personal_assistant.infrastructure.config_env import (
    _env,
    _env_bool,
    _env_permission_tier,
    _finite_seconds,
    _load_env_file,
    _optional_env,
    _parse_csv,
)
from personal_assistant.infrastructure.config_loader_llm import _load_llm_kwargs
from personal_assistant.infrastructure.config_loader_media import _load_media_kwargs
from personal_assistant.infrastructure.config_loader_whatsapp import (
    _load_whatsapp_kwargs,
)

if TYPE_CHECKING:
    from personal_assistant.infrastructure.config_settings import AppSettings

T = TypeVar("T", bound="AppSettings")


def load_app_settings_from_env(cls: type[T]) -> T:
    """Instantiate AppSettings from loaded environment values."""
    file_values = _load_env_file()
    interval = _env("REMINDER_WORKER_INTERVAL_SECONDS", file_values, "15")
    heartbeat_timeout = _env(
        "REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS", file_values, "45"
    )
    reminder_minutes_before = _env("REMINDER_MINUTES_BEFORE", file_values, "30")
    trace_retention_days = _env(
        "TRACE_RETENTION_DAYS",
        file_values,
        str(DEFAULT_TRACE_RETENTION_DAYS),
    )

    llm_kwargs = _load_llm_kwargs(file_values)
    media_kwargs = _load_media_kwargs(file_values)

    return cls(
        tenant_id=_env("ASSISTANT_TENANT_ID", file_values, "personal").strip()
        or "personal",
        timezone=_env("ASSISTANT_TIMEZONE", file_values, "America/Bogota").strip()
        or "America/Bogota",
        reply_locale=_env("ASSISTANT_REPLY_LOCALE", file_values, "es").strip()
        or "es",
        persistence_backend=_env("PERSISTENCE_BACKEND", file_values, "memory")
        .strip()
        .lower()
        or "memory",
        database_url=_optional_env("DATABASE_URL", file_values),
        database_schema=_env(
            "DATABASE_SCHEMA", file_values, DEFAULT_DATABASE_SCHEMA
        ).strip()
        or DEFAULT_DATABASE_SCHEMA,
        telegram_webhook_secret=_env(
            "TELEGRAM_WEBHOOK_SECRET", file_values
        ).strip(),
        telegram_bot_token=_optional_env("TELEGRAM_BOT_TOKEN", file_values),
        telegram_allowed_user_ids=_parse_csv(
            _env("TELEGRAM_ALLOWED_USER_IDS", file_values)
        ),
        telegram_audio_reply_mode=_env(
            "TELEGRAM_AUDIO_REPLY_MODE", file_values, "disabled"
        )
        .strip()
        .lower()
        or "disabled",
        admin_token=_optional_env("ADMIN_TOKEN", file_values),
        local_auth_principal_id=_env(
            "LOCAL_AUTH_PRINCIPAL_ID", file_values, "local-user"
        ).strip(),
        local_auth_permission_tier=_env_permission_tier(
            "LOCAL_AUTH_PERMISSION_TIER",
            file_values,
            PermissionTier.P5,
        ),
        public_base_url=_optional_env("PUBLIC_BASE_URL", file_values),
        reminder_worker_enabled=_env_bool("REMINDER_WORKER_ENABLED", file_values),
        reminder_worker_interval_seconds=_finite_seconds(
            "REMINDER_WORKER_INTERVAL_SECONDS", interval
        ),
        reminder_worker_heartbeat_timeout_seconds=_finite_seconds(
            "REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS", heartbeat_timeout
        ),
        reminder_minutes_before=max(int(reminder_minutes_before), 1),
        trace_retention_days=int(trace_retention_days),
        egress_allowed_hosts=_parse_csv(
            _env("EGRESS_ALLOWED_HOSTS", file_values)
        ),
        whatsapp=_load_whatsapp_kwargs(file_values),
        **llm_kwargs,
        **media_kwargs,
    )
