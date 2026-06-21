"""Framework-light webhook entry points.

FastAPI can wrap these functions later. Keeping the core functions free of web
framework imports makes the MVP testable without network-installed dependencies.
"""

from __future__ import annotations

from typing import Any

from personal_assistant.channels.models import NormalizedMessage
from personal_assistant.channels.telegram import TelegramAdapter
from personal_assistant.channels.whatsapp import WhatsAppAdapter


def normalize_telegram_webhook(payload: dict[str, Any], *, tenant_id: str) -> NormalizedMessage:
    return TelegramAdapter().normalize_webhook(payload, tenant_id=tenant_id)


def normalize_whatsapp_webhook(payload: dict[str, Any], *, tenant_id: str) -> NormalizedMessage:
    return WhatsAppAdapter().normalize_webhook(payload, tenant_id=tenant_id)

