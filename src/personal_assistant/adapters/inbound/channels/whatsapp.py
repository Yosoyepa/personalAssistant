"""WhatsApp Cloud API webhook normalization."""

from __future__ import annotations

from typing import Any

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage


class WhatsAppAdapter:
    """Normalizes WhatsApp webhook payloads for later MVP phase."""

    channel = ChannelName.whatsapp

    def normalize_webhook(self, payload: dict[str, Any], *, tenant_id: str) -> NormalizedMessage:
        if not tenant_id:
            raise ValueError("tenant_id is required from authenticated channel config")

        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value") or {}
        message = (value.get("messages") or [{}])[0]
        contact = (value.get("contacts") or [{}])[0]
        text = (message.get("text") or {}).get("body") or ""
        actor_id = str(contact.get("wa_id") or message.get("from") or "")
        message_id = str(message.get("id") or "")
        return NormalizedMessage(
            channel=self.channel,
            actor_id=actor_id,
            conversation_id=actor_id,
            message_id=message_id,
            text=text,
        )
