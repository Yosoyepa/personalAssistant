from __future__ import annotations

from personal_assistant.infrastructure.config import AppSettings


def test_app_settings_repr_omits_sensitive_values() -> None:
    sentinels: dict[str, object] = {
        "tenant_id": "sentinel-tenant-id",
        "database_url": "postgresql://sentinel-db-user:sentinel-password@db/private",
        "telegram_webhook_secret": "sentinel-telegram-webhook-secret",
        "telegram_bot_token": "123456789:sentinel-telegram-bot-token",
        "telegram_allowed_user_ids": frozenset(
            {"sentinel-telegram-user-id", "sentinel-telegram-chat-id"}
        ),
        "llm_api_key": "sentinel-llm-api-key",
        "llm_base_url": "https://sentinel-llm-provider.invalid/private",
        "transcription_api_key": "sentinel-transcription-api-key",
        "transcription_base_url": (
            "https://sentinel-transcription-provider.invalid/private"
        ),
        "tts_api_key": "sentinel-tts-api-key",
        "tts_base_url": "https://sentinel-tts-provider.invalid/private",
        "admin_token": "sentinel-admin-token",
        "local_auth_principal_id": "sentinel-local-principal-id",
        "public_base_url": "https://sentinel-public-edge.invalid/private",
    }

    rendered = repr(AppSettings(**sentinels))  # type: ignore[arg-type]

    for field_name, value in sentinels.items():
        assert field_name not in rendered
        values = value if isinstance(value, frozenset) else (value,)
        for sentinel in values:
            assert str(sentinel) not in rendered

    assert "timezone='America/Bogota'" in rendered
    assert "persistence_backend='memory'" in rendered
