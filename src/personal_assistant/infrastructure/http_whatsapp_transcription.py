"""WhatsApp audio transcription pipeline."""

from __future__ import annotations

from personal_assistant.adapters.outbound.notifications.whatsapp import (
    WhatsAppGraphApiClient,
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
    MAX_WHATSAPP_AUDIO_BYTES,
    SUPPORTED_TRANSCRIPTION_EXTENSIONS,
)
from personal_assistant.infrastructure.http_dynamic import get_http_attribute


def _transcription_filename(message: NormalizedMessage, media_url: str) -> str:
    """Determine a safe transcription filename based on URL extension and MIME type."""
    clean_url = media_url.split("?", 1)[0]
    extension = clean_url.rsplit(".", 1)[-1].lower() if "." in clean_url else ""
    if extension == "oga":
        extension = "ogg"
    elif extension not in SUPPORTED_TRANSCRIPTION_EXTENSIONS:
        if message.media_mime_type and "ogg" in message.media_mime_type:
            extension = "ogg"
        elif message.media_mime_type and "opus" in message.media_mime_type:
            extension = "opus"
        elif message.media_mime_type and "mp3" in message.media_mime_type:
            extension = "mp3"
        elif message.media_mime_type and "mp4" in message.media_mime_type:
            extension = "mp4"
        elif message.media_mime_type and "wav" in message.media_mime_type:
            extension = "wav"
        else:
            extension = "ogg"
    return f"whatsapp-{message.message_id}.{extension}"


def _transcribe_whatsapp_media(
    container: AppContainer,
    settings: AppSettings,
    message: NormalizedMessage,
    replies: AssistantReplies,
) -> tuple[NormalizedMessage | None, str | None]:
    """Download audio file from WhatsApp, transcribe it, and return updated message."""
    if not message.media_file_id:
        return None, replies.whatsapp_audio_missing_file_id()
    if container.transcription is None:
        return None, replies.whatsapp_transcription_not_configured()
    if not settings.whatsapp.access_token:
        return None, replies.whatsapp_media_download_failed()
    if (
        message.media_file_size is not None
        and message.media_file_size > MAX_WHATSAPP_AUDIO_BYTES
    ):
        return None, replies.whatsapp_audio_too_large()

    transcription_filename: str | None = None
    client_cls = get_http_attribute("WhatsAppGraphApiClient", WhatsAppGraphApiClient)
    try:
        client = client_cls(
            access_token=settings.whatsapp.access_token,
            phone_number_id=settings.whatsapp.phone_number_id,
            egress_allowlist=build_egress_allowlist(settings),
        )
        media_url = client.get_media_url(media_id=message.media_file_id)
        audio = client.download_media(url=media_url)
        if len(audio) > MAX_WHATSAPP_AUDIO_BYTES:
            return None, replies.whatsapp_audio_download_too_large()

        transcription_filename = _transcription_filename(message, media_url)
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
                run_id=f"whatsapp:{message.conversation_id}:{message.message_id}:transcription",
                agent_id="personal_assistant",
                event_type=TraceEventType.tool_called,
                tenant_id=settings.tenant_id,
                input_summary={
                    "media_kind": message.media_kind,
                    "media_mime_type": message.media_mime_type,
                    "media_file_size": message.media_file_size,
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
                run_id=f"whatsapp:{message.conversation_id}:{message.message_id}:transcription",
                agent_id="personal_assistant",
                event_type=TraceEventType.agent_failed,
                tenant_id=settings.tenant_id,
                input_summary={
                    "media_kind": message.media_kind,
                    "media_mime_type": message.media_mime_type,
                    "media_file_size": message.media_file_size,
                    "transcription_filename": transcription_filename,
                },
                error={"type": exc.__class__.__name__, "message": str(exc)[:500]},
            )
        )
        return None, replies.whatsapp_transcription_failed()

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
