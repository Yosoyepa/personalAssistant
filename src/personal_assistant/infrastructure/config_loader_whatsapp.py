"""WhatsApp settings loader from environment variables."""

from __future__ import annotations

from personal_assistant.infrastructure.config_env import (
    _env,
    _env_bool,
    _parse_csv,
)
from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings


def _load_whatsapp_kwargs(file_values: dict[str, str]) -> WhatsAppSettings:
    return WhatsAppSettings(
        enabled=_env_bool("WHATSAPP_ENABLED", file_values),
        app_secret=_env("WHATSAPP_APP_SECRET", file_values).strip(),
        verify_token=_env("WHATSAPP_VERIFY_TOKEN", file_values).strip(),
        allowed_user_ids=_parse_csv(
            _env("WHATSAPP_ALLOWED_USER_IDS", file_values)
        ),
    )
