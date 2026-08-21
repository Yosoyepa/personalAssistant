"""WhatsApp Cloud API webhook normalization."""

from __future__ import annotations

from typing import Any

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage

_SUPPORTED_MEDIA_TYPES = frozenset({"audio", "voice", "image", "document", "video"})


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class WhatsAppAdapter:
    """Normalizes WhatsApp webhook payloads for the conversation pipeline."""

    channel = ChannelName.whatsapp

    def normalize_webhook(
        self, payload: dict[str, Any], *, tenant_id: str
    ) -> NormalizedMessage:
        if not tenant_id:
            raise ValueError("tenant_id is required from authenticated channel config")

        entries = payload.get("entry") or []
        entry = _mapping(entries[0]) if entries else {}
        changes = entry.get("changes") or []
        change = _mapping(changes[0]) if changes else {}
        value = _mapping(change.get("value"))
        messages = value.get("messages") or []
        message = _mapping(messages[0]) if messages else {}
        contacts = value.get("contacts") or []
        contact = _mapping(contacts[0]) if contacts else {}

        raw_type = str(message.get("type") or "").strip().lower()
        media_dict = (
            _mapping(message.get(raw_type))
            if raw_type in _SUPPORTED_MEDIA_TYPES
            else (
                _mapping(message.get("voice"))
                or _mapping(message.get("audio"))
                or _mapping(message.get("image"))
                or _mapping(message.get("document"))
                or _mapping(message.get("video"))
            )
        )
        media_kind: str | None = None
        if raw_type in _SUPPORTED_MEDIA_TYPES:
            media_kind = raw_type
        elif media_dict:
            for kind in ("voice", "audio", "image", "document", "video"):
                if message.get(kind):
                    media_kind = kind
                    break

        media_file_id = str(media_dict.get("id") or "").strip() or None
        media_mime_type = str(media_dict.get("mime_type") or "").strip() or None
        raw_file_size = media_dict.get("file_size")
        media_file_size: int | None = (
            int(raw_file_size) if raw_file_size is not None else None
        )

        caption = str(media_dict.get("caption") or "")
        text = str((_mapping(message.get("text"))).get("body") or caption or "")
        if not text and media_kind:
            text = f"[{media_kind} message]"

        actor_id = str(contact.get("wa_id") or message.get("from") or "")
        message_id = str(message.get("id") or "")
        idempotency_key = f"whatsapp:{message_id}" if message_id else None
        return NormalizedMessage(
            channel=self.channel,
            actor_id=actor_id,
            conversation_id=actor_id,
            message_id=message_id,
            source_event_id=message_id,
            text=text,
            idempotency_key=idempotency_key,
            media_kind=media_kind,
            media_file_id=media_file_id,
            media_mime_type=media_mime_type,
            media_file_size=media_file_size,
        )
