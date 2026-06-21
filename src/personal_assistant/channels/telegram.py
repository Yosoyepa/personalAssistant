"""Telegram webhook normalization."""

from __future__ import annotations

from typing import Any

from personal_assistant.channels.models import ChannelName, NormalizedMessage


class TelegramAdapter:
    """Normalizes Telegram updates without owning external send side effects."""

    channel = ChannelName.telegram

    def normalize_webhook(self, payload: dict[str, Any], *, tenant_id: str) -> NormalizedMessage:
        message = payload.get("message") or payload.get("edited_message") or {}
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        text = message.get("text") or message.get("caption") or ""
        message_id = str(message.get("message_id") or payload.get("update_id") or "")
        conversation_id = str(chat.get("id") or "")
        actor_id = str(user.get("id") or conversation_id)
        if not tenant_id:
            raise ValueError("tenant_id is required from authenticated channel config")
        return NormalizedMessage(
            channel=self.channel,
            tenant_id=tenant_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text=text,
            raw=payload,
        )

