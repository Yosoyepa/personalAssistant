"""Integration and unit tests for WhatsApp Cloud API inbound media webhooks (Phase 25)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import (
    AgentStatus,
    AudioTranscriptionRequest,
    AudioTranscriptionResult,
)
from personal_assistant.application.dto.tracing import TraceEventType
from personal_assistant.application.ports.services import (
    AudioTranscriptionProvider,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer, build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings
from personal_assistant.infrastructure.http import create_app

WEBHOOK_SECRET = "test_wa_app_secret"
VERIFY_TOKEN = "test_wa_verify_token"


def _sign_payload(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_whatsapp_media_payload(
    wa_id: str,
    media_kind: str,
    media_id: str = "media-1001",
    mime_type: str = "audio/ogg",
    file_size: int | None = None,
    caption: str | None = None,
    message_id: str = "wamid.media123",
) -> dict[str, Any]:
    media_obj: dict[str, Any] = {
        "id": media_id,
        "mime_type": mime_type,
    }
    if file_size is not None:
        media_obj["file_size"] = file_size
    if caption is not None:
        media_obj["caption"] = caption

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100000000000001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001",
                                "phone_number_id": "100000000000002",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": wa_id,
                                }
                            ],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": message_id,
                                    "timestamp": "1723654800",
                                    "type": media_kind,
                                    media_kind: media_obj,
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


class _FakeTranscriptionProvider(AudioTranscriptionProvider):
    def __init__(
        self,
        transcript_text: str = "recuérdame mañana a las 17 cerrar caja",
        should_fail: bool = False,
    ) -> None:
        self.transcript_text = transcript_text
        self.should_fail = should_fail
        self.calls: list[AudioTranscriptionRequest] = []

    def transcribe(
        self, request: AudioTranscriptionRequest, *, budget: TokenBudget
    ) -> AudioTranscriptionResult:
        self.calls.append(request)
        if self.should_fail:
            raise RuntimeError("Transcription provider timeout")
        return AudioTranscriptionResult(
            text=self.transcript_text,
            provider="mock_transcription",
            model="mock-whisper",
        )


WA_ACCESS_TOKEN_FIXTURE = "test_wa_token"


def _client(
    container: AppContainer,
    *,
    allowed_numbers: tuple[str, ...] = ("573001112233", "573004445566"),
    wa_token: str = WA_ACCESS_TOKEN_FIXTURE,
) -> TestClient:
    settings = AppSettings(
        tenant_id="personal",
        timezone="America/Bogota",
        reminder_worker_enabled=False,
        whatsapp=WhatsAppSettings(
            enabled=True,
            app_secret=WEBHOOK_SECRET,
            verify_token=VERIFY_TOKEN,
            access_token=wa_token,
            phone_number_id="100000000000002",
            allowed_user_ids=frozenset(allowed_numbers),
        ),
    )
    return TestClient(create_app(container, settings=settings))


def test_signed_voice_note_is_transcribed_and_handled_as_a_reminder() -> None:
    transcription = _FakeTranscriptionProvider("recuérdame mañana a las 17 cerrar caja")
    container = build_container(transcription=transcription)
    client = _client(container)

    for media_kind, size_kb in [("audio", 120), ("voice", 340)]:
        payload = _make_whatsapp_media_payload(
            "573001112233",
            media_kind=media_kind,
            media_id=f"id-{media_kind}",
            file_size=size_kb * 1024,
            message_id=f"wamid.{media_kind}.{size_kb}",
        )
        body = json.dumps(payload).encode("utf-8")
        signature = _sign_payload(body)

        with (
            patch(
                "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url",
                return_value="https://lookaside.fbsbx.com/media/audio.ogg",
            ) as mock_get_url,
            patch(
                "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media",
                return_value=b"OGG_AUDIO_BYTES_TEST",
            ) as mock_download,
            patch(
                "personal_assistant.infrastructure.http_whatsapp_replies._send_whatsapp_reply",
                return_value=True,
            ),
        ):
            response = client.post(
                "/webhooks/whatsapp",
                content=body,
                headers={"x-hub-signature-256": signature},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] in (
            AgentStatus.completed.value,
            AgentStatus.escalated.value,
        )
        assert data["reply"] is not None
        assert mock_get_url.call_count == 1
        assert mock_download.call_count == 1

        # Check trace written
        tool_traces = [
            t
            for t in container.traces.list_for_tenant(
                Principal.for_test(
                    principal_id="573001112233",
                    tenant_id="personal",
                    permission_tier=PermissionTier.P2,
                )
            )
            if t.event_type == TraceEventType.tool_called
        ]
        assert len(tool_traces) >= 1
        assert tool_traces[-1].output_summary.get("text_length") == len(
            "recuérdame mañana a las 17 cerrar caja"
        )


def test_oversized_audio_is_rejected_with_an_explicit_reply_before_download() -> None:
    transcription = _FakeTranscriptionProvider()
    container = build_container(transcription=transcription)
    client = _client(container)

    for wa_number, size_mb in [("573001112233", 21), ("573004445566", 100)]:
        payload = _make_whatsapp_media_payload(
            wa_number,
            media_kind="audio",
            file_size=size_mb * 1024 * 1024,
            message_id=f"wamid.oversized.{size_mb}",
        )
        body = json.dumps(payload).encode("utf-8")
        signature = _sign_payload(body)

        with (
            patch(
                "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url"
            ) as mock_get_url,
            patch(
                "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media"
            ) as mock_download,
        ):
            response = client.post(
                "/webhooks/whatsapp",
                content=body,
                headers={"x-hub-signature-256": signature},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == AgentStatus.needs_clarification.value
        assert "supera el límite local de 20MB" in data["reply"]
        assert mock_get_url.call_count == 0
        assert mock_download.call_count == 0


def test_audio_whose_downloaded_bytes_exceed_limit_is_rejected_after_download() -> None:
    transcription = _FakeTranscriptionProvider()
    container = build_container(transcription=transcription)
    client = _client(container)

    payload = _make_whatsapp_media_payload(
        "573001112233",
        media_kind="voice",
        file_size=1024 * 1024,  # declares 1MB
        message_id="wamid.spoofed.size",
    )
    body = json.dumps(payload).encode("utf-8")
    signature = _sign_payload(body)

    with (
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url",
            return_value="https://lookaside.fbsbx.com/media/audio.ogg",
        ),
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media",
            return_value=b"X" * (20 * 1024 * 1024 + 1),  # actual download > 20MB
        ),
    ):
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == AgentStatus.needs_clarification.value
    assert "descargado supera el límite local de 20MB" in data["reply"]
    assert len(transcription.calls) == 0


def test_transcription_unavailable_produces_an_explicit_reply() -> None:
    container = build_container(transcription=None)
    client = _client(container)

    payload = _make_whatsapp_media_payload(
        "573001112233",
        media_kind="voice",
        file_size=100 * 1024,
        message_id="wamid.not_configured",
    )
    body = json.dumps(payload).encode("utf-8")
    signature = _sign_payload(body)

    with (
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url"
        ) as mock_get_url,
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media"
        ) as mock_download,
    ):
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == AgentStatus.needs_clarification.value
    assert "falta configurar transcripción" in data["reply"]
    assert mock_get_url.call_count == 0
    assert mock_download.call_count == 0


def test_transcription_failure_produces_explicit_reply_and_failure_trace() -> None:
    transcription = _FakeTranscriptionProvider(should_fail=True)
    container = build_container(transcription=transcription)
    client = _client(container)

    payload = _make_whatsapp_media_payload(
        "573001112233",
        media_kind="voice",
        file_size=100 * 1024,
        message_id="wamid.fail_whisper",
    )
    body = json.dumps(payload).encode("utf-8")
    signature = _sign_payload(body)

    with (
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url",
            return_value="https://lookaside.fbsbx.com/media/audio.ogg",
        ),
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media",
            return_value=b"OGG_AUDIO_BYTES_TEST",
        ),
    ):
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == AgentStatus.needs_clarification.value
    assert "No pude transcribir ese audio" in data["reply"]

    # Check agent_failed trace
    failed_traces = [
        t
        for t in container.traces.list_for_tenant(
            Principal.for_test(
                principal_id="573001112233",
                tenant_id="personal",
                permission_tier=PermissionTier.P2,
            )
        )
        if t.event_type == TraceEventType.agent_failed
    ]
    assert len(failed_traces) >= 1
    assert failed_traces[-1].error.get("type") == "RuntimeError"


def test_non_audio_media_gets_an_explicit_unsupported_reply() -> None:
    transcription = _FakeTranscriptionProvider()
    container = build_container(transcription=transcription)
    client = _client(container)

    for media_kind in ["image", "document", "video"]:
        payload = _make_whatsapp_media_payload(
            "573001112233",
            media_kind=media_kind,
            media_id=f"id-{media_kind}",
            message_id=f"wamid.unsupported.{media_kind}",
        )
        body = json.dumps(payload).encode("utf-8")
        signature = _sign_payload(body)

        with (
            patch(
                "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url"
            ) as mock_get_url,
            patch(
                "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media"
            ) as mock_download,
        ):
            response = client.post(
                "/webhooks/whatsapp",
                content=body,
                headers={"x-hub-signature-256": signature},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == AgentStatus.needs_clarification.value
        assert "solo puedo procesar mensajes de texto y notas de voz" in data["reply"]
        assert mock_get_url.call_count == 0
        assert mock_download.call_count == 0


def test_media_from_unauthorized_sender_is_rejected_before_any_download() -> None:
    """Security Invariant: HMAC -> sender allowlist (403) -> network."""
    transcription = _FakeTranscriptionProvider()
    container = build_container(transcription=transcription)
    client = _client(container, allowed_numbers=("573001112233",))

    payload = _make_whatsapp_media_payload(
        "573009999999",  # Unknown / unauthorized sender
        media_kind="voice",
        media_id="media-unauthorized",
        message_id="wamid.unauthorized",
    )
    body = json.dumps(payload).encode("utf-8")
    signature = _sign_payload(body)

    with (
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url"
        ) as mock_get_url,
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media"
        ) as mock_download,
    ):
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )

    assert response.status_code == 403, response.text
    assert mock_get_url.call_count == 0
    assert mock_download.call_count == 0
    assert len(transcription.calls) == 0


def test_replayed_delivery_of_same_audio_message_creates_no_duplicate() -> None:
    transcription = _FakeTranscriptionProvider(
        "recuérdame mañana a las 17 comprar leche"
    )
    container = build_container(transcription=transcription)
    client = _client(container)

    payload = _make_whatsapp_media_payload(
        "573001112233",
        media_kind="voice",
        media_id="media-voice-replay",
        message_id="wamid.replay.123",
    )
    body = json.dumps(payload).encode("utf-8")
    signature = _sign_payload(body)

    with (
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.get_media_url",
            return_value="https://lookaside.fbsbx.com/media/audio.ogg",
        ),
        patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.WhatsAppGraphApiClient.download_media",
            return_value=b"OGG_AUDIO_BYTES_REPLAY",
        ),
    ):
        first_resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )
        replay_resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": signature},
        )

    assert first_resp.status_code == 200, first_resp.text
    assert replay_resp.status_code == 200, replay_resp.text
    assert first_resp.json()["reply"] == replay_resp.json()["reply"]

    # Verify reminder exists exactly once in storage
    principal = Principal.for_test(
        principal_id="573001112233",
        tenant_id="personal",
        permission_tier=PermissionTier.P2,
    )
    approvals = container.approvals.list_for_tenant(principal)
    assert len(approvals) == 1
    assert len(container.states.list_for_tenant(principal)) == 1
