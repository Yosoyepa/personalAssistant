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
    admin_token: str | None = None
    public_base_url: str | None = None
    reminder_worker_interval_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "AppSettings":
        interval = os.getenv("REMINDER_WORKER_INTERVAL_SECONDS", "15")
        return cls(
            tenant_id=os.getenv("ASSISTANT_TENANT_ID", "personal").strip() or "personal",
            timezone=os.getenv("ASSISTANT_TIMEZONE", "America/Bogota").strip() or "America/Bogota",
            telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", "local-dev-secret").strip()
            or "local-dev-secret",
            telegram_bot_token=_optional_env("TELEGRAM_BOT_TOKEN"),
            telegram_allowed_user_ids=_parse_csv(os.getenv("TELEGRAM_ALLOWED_USER_IDS")),
            admin_token=_optional_env("ADMIN_TOKEN"),
            public_base_url=_optional_env("PUBLIC_BASE_URL"),
            reminder_worker_interval_seconds=max(float(interval), 1.0),
        )


def _parse_csv(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())
