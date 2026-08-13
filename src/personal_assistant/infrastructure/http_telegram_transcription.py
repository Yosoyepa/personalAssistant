"""Telegram audio transcription pipeline."""

from __future__ import annotations

from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
)
from personal_assistant.application.dto.channels import NormalizedMessage
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AudioTranscriptionRequest
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.infrastructure.bootstrap import (
    AppContainer,
    build_egress_allowlist,
)
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http_auth import (
    MAX_TELEGRAM_AUDIO_BYTES,
    SUPPORTED_TRANSCRIPTION_EXTENSIONS,
)
from personal_assistant.infrastructure.http_dynamic import get_http_attribute


def _transcription_filename(message: NormalizedMessage, file_path: str) -> str:
    """Determine a safe transcription filename based on file extension and MIME type."""
    extension = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if extension == "oga":
        extension = "ogg"
    elif extension not in SUPPORTED_TRANSCRIPTION_EXTENSIONS:
        if message.media_mime_type == "audio/ogg":
            extension = "ogg"
        elif message.media_mime_type == "audio/opus":
            extension = "opus"
        else:
            extension = "ogg"
    return f"telegram-{message.message_id}.{extension}"


def _transcribe_telegram_media(
    container: AppContainer,
    settings: AppSettings,
    message: NormalizedMessage,
    replies: AssistantReplies,
) -> tuple[NormalizedMessage | None, str | None]:
    """Download audio file from Telegram, transcribe it, and return updated message."""
    if not message.media_file_id:
        return None, replies.telegram_audio_missing_file_id()
    if container.transcription is None:
        return None, replies.telegram_transcription_not_configured()
    if not settings.telegram_bot_token:
        return None, replies.telegram_token_missing_for_audio()
    if (
        message.media_file_size is not None
        and message.media_file_size > MAX_TELEGRAM_AUDIO_BYTES
    ):
        return None, replies.telegram_audio_too_large()

    transcription_filename: str | None = None
    telegram_file_extension: str | None = None
    client_cls = get_http_attribute("TelegramBotApiClient", TelegramBotApiClient)
    try:
        client = client_cls(
            token=settings.telegram_bot_token,
            egress_allowlist=build_egress_allowlist(settings),
        )
        file_info = client.get_file(file_id=message.media_file_id)
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            return None, replies.telegram_file_path_missing()
        audio = client.download_file(file_path=file_path)
        if len(audio) > MAX_TELEGRAM_AUDIO_BYTES:
            return None, replies.telegram_audio_download_too_large()

        telegram_file_extension = (
            file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
        )
        transcription_filename = _transcription_filename(message, file_path)
        transcript = container.transcription.transcribe(
            AudioTranscriptionRequest(
                filename=transcription_filename,
                content_type=message.media_mime_type or "audio/ogg",
                data=audio,
                language="es",
                prompt=container.prompt_catalog.render(
                    "telegram_voice_transcription", {}
                ).text,
            ),
            budget=TokenBudget(limit=4_000),
        )
        container.traces.write(
            TraceEvent(
                run_id=f"telegram:{message.conversation_id}:{message.message_id}:transcription",
                agent_id="personal_assistant",
                event_type=TraceEventType.tool_called,
                tenant_id=settings.tenant_id,
                input_summary={
                    "media_kind": message.media_kind,
                    "media_mime_type": message.media_mime_type,
                    "media_file_size": message.media_file_size,
                    "telegram_file_extension": telegram_file_extension,
                    "transcription_filename": transcription_filename,
                },
                tool_call={"name": "audio.transcribe", "provider": transcript.provider},
                model=transcript.model,
                output_summary={
                    "transcript": transcript.text[:500],
                    "text_length": len(transcript.text),
                },
            )
        )
    except Exception as exc:
        container.traces.write(
            TraceEvent(
                run_id=f"telegram:{message.conversation_id}:{message.message_id}:transcription",
                agent_id="personal_assistant",
                event_type=TraceEventType.agent_failed,
                tenant_id=settings.tenant_id,
                input_summary={
                    "media_kind": message.media_kind,
                    "media_mime_type": message.media_mime_type,
                    "media_file_size": message.media_file_size,
                    "telegram_file_extension": telegram_file_extension,
                    "transcription_filename": transcription_filename,
                },
                error={"type": exc.__class__.__name__, "message": str(exc)[:500]},
            )
        )
        return None, replies.telegram_transcription_failed()
    return (
        message.model_copy(
            update={
                "text": transcript.text,
                "command": None,
                "command_args": "",
            }
        ),
        None,
    )
