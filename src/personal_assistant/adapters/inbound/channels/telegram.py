"""Telegram webhook normalization."""

from __future__ import annotations

import re
from typing import Any

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage


_COMMAND_RE = re.compile(r"^[A-Za-z0-9_]+$")


class TelegramActorNotVerifiableError(ValueError):
    """Raised when an update has no Telegram user identity."""


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

    def normalize_webhook(
        self, payload: dict[str, Any], *, tenant_id: str
    ) -> NormalizedMessage:
        raw_callback_query = payload.get("callback_query")
        callback_query = (
            raw_callback_query if isinstance(raw_callback_query, dict) else {}
        )
        message = (
            _mapping(payload.get("message"))
            or _mapping(payload.get("edited_message"))
            or _mapping(callback_query.get("message"))
            or {}
        )
        chat = _mapping(message.get("chat"))
        raw_user = callback_query.get("from") if callback_query else message.get("from")
        user = _mapping(raw_user)
        voice = _mapping(message.get("voice"))
        audio = _mapping(message.get("audio"))
        media = voice or audio
        media_kind = "voice" if voice else "audio" if audio else None
        media_file_id = str(media.get("file_id") or "") if media else None
        media_mime_type = str(media.get("mime_type") or "audio/ogg") if media else None
        media_file_size = media.get("file_size") if media else None
        text = (
            callback_query.get("data")
            or message.get("text")
            or message.get("caption")
            or ""
        )
        if not text and media_file_id:
            text = f"[{media_kind} message]"
        callback_event_id = str(callback_query.get("id") or "")
        message_id = str(
            message.get("message_id")
            or callback_event_id
            or payload.get("update_id")
            or ""
        )
        conversation_id = str(chat.get("id") or "")
        actor_id = str(user.get("id") or "").strip()
        if not actor_id:
            raise TelegramActorNotVerifiableError(
                "telegram update has no verifiable actor"
            )
        update_id = payload.get("update_id")
        # Telegram webhooks normally carry update_id. Callback ids are the
        # stable provider-event fallback when a callback fixture/provider omits
        # update_id; the referenced message id remains a separate dimension.
        source_event_id = (
            str(update_id)
            if update_id is not None
            else callback_event_id or f"message:{conversation_id}:{message_id}"
        )
        idempotency_key = f"telegram:{source_event_id}"
        command, command_args = _parse_command(str(text))
        if not tenant_id:
            raise ValueError("tenant_id is required from authenticated channel config")
        return NormalizedMessage(
            channel=self.channel,
            actor_id=actor_id,
            conversation_id=conversation_id,
            message_id=message_id,
            source_event_id=source_event_id,
            text=str(text),
            idempotency_key=idempotency_key,
            command=command,
            command_args=command_args,
            media_kind=media_kind,
            media_file_id=media_file_id or None,
            media_mime_type=media_mime_type,
            media_file_size=int(media_file_size)
            if media_file_size is not None
            else None,
        )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
