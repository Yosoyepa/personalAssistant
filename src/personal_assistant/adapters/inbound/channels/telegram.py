"""Telegram webhook normalization."""

from __future__ import annotations

import re
from typing import Any

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage


_COMMAND_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _parse_command(text: str) -> tuple[str | None, str]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, ""
    token, _, args = stripped.partition(" ")
    command = token[1:].split("@", 1)[0].strip().lower()
    if not command or _COMMAND_RE.fullmatch(command) is None:
        return None, ""
    return command, args.strip()


class TelegramAdapter:
    """Normalizes Telegram updates without owning external send side effects."""

    channel = ChannelName.telegram

    def normalize_webhook(self, payload: dict[str, Any], *, tenant_id: str) -> NormalizedMessage:
        callback_query = payload.get("callback_query") or {}
        message = payload.get("message") or payload.get("edited_message") or callback_query.get("message") or {}
        chat = message.get("chat") or {}
        user = callback_query.get("from") or message.get("from") or {}
        voice = message.get("voice") or {}
        audio = message.get("audio") or {}
        media = voice or audio
        media_kind = "voice" if voice else "audio" if audio else None
        media_file_id = str(media.get("file_id") or "") if media else None
        media_mime_type = str(media.get("mime_type") or "audio/ogg") if media else None
        media_file_size = media.get("file_size") if media else None
        text = callback_query.get("data") or message.get("text") or message.get("caption") or ""
        if not text and media_file_id:
            text = f"[{media_kind} message]"
        message_id = str(callback_query.get("id") or message.get("message_id") or payload.get("update_id") or "")
        conversation_id = str(chat.get("id") or "")
        actor_id = str(user.get("id") or conversation_id)
        update_id = payload.get("update_id")
        idempotency_key = (
            f"telegram:{update_id}"
            if update_id is not None
            else f"telegram:{conversation_id}:{message_id}"
        )
        command, command_args = _parse_command(str(text))
        if not tenant_id:
            raise ValueError("tenant_id is required from authenticated channel config")
        return NormalizedMessage(
            channel=self.channel,
            actor_id=actor_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text=str(text),
            idempotency_key=idempotency_key,
            command=command,
            command_args=command_args,
            media_kind=media_kind,
            media_file_id=media_file_id or None,
            media_mime_type=media_mime_type,
            media_file_size=int(media_file_size) if media_file_size is not None else None,
        )
