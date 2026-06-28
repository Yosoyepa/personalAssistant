from __future__ import annotations

import unittest

from personal_assistant.adapters.outbound.notifications.telegram import TelegramNotificationTool
from personal_assistant.application.ports.notifications import NotificationMedia, NotificationRequest
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.sent_audio: list[dict[str, object]] = []

    def send_message(self, *, chat_id: str, text: str) -> dict[str, int]:
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.sent)}

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, int]:
        self.sent_audio.append(
            {
                "chat_id": chat_id,
                "caption": caption,
                "filename": filename,
                "content_type": content_type,
                "data": data,
            }
        )
        return {"message_id": 100 + len(self.sent_audio)}


class TelegramNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeTelegramClient()
        self.tool = TelegramNotificationTool(self.client)
        self.principal = Principal.for_test(
            principal_id="telegram-user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.request = NotificationRequest(
            channel="telegram",
            recipient="chat-1",
            body="Recordatorio",
            idempotency_key="msg-1",
        )

    def approval(self) -> ApprovalGrant:
        return ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=self.request.idempotency_key,
            tier=PermissionTier.P5,
        )

    def test_telegram_send_requires_p5_approval_before_dispatch_or_replay(self) -> None:
        with self.assertRaises(AssistantError) as missing:
            self.tool.send(self.principal, self.request)

        self.assertEqual(missing.exception.code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(self.client.sent, [])

        sent = self.tool.send(self.principal, self.request, approval=self.approval())
        with self.assertRaises(AssistantError):
            self.tool.send(self.principal, self.request)
        reused = self.tool.send(self.principal, self.request, approval=self.approval())

        self.assertEqual(sent.notification_id, reused.notification_id)
        self.assertTrue(reused.reused)
        self.assertEqual(len(self.client.sent), 1)

    def test_telegram_send_rejects_idempotency_conflict(self) -> None:
        self.tool.send(self.principal, self.request, approval=self.approval())
        conflict = self.request.model_copy(update={"body": "Otro texto"})

        with self.assertRaises(AssistantError) as ctx:
            self.tool.send(self.principal, conflict, approval=self.approval())

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(len(self.client.sent), 1)

    def test_telegram_send_audio_uses_media_payload_and_idempotency(self) -> None:
        request = NotificationRequest(
            channel="telegram",
            recipient="chat-1",
            body="Listo, quedo agendado.",
            idempotency_key="msg-voice-1",
            media=NotificationMedia(
                filename="assistant-reply.mp3",
                content_type="audio/mpeg",
                data=b"mp3-bytes",
            ),
        )
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=request.idempotency_key,
            tier=PermissionTier.P5,
        )

        sent = self.tool.send(self.principal, request, approval=approval)
        reused = self.tool.send(self.principal, request, approval=approval)

        self.assertEqual(sent.notification_id, reused.notification_id)
        self.assertTrue(reused.reused)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(len(self.client.sent_audio), 1)
        self.assertEqual(self.client.sent_audio[0]["filename"], "assistant-reply.mp3")
        self.assertEqual(self.client.sent_audio[0]["data"], b"mp3-bytes")


if __name__ == "__main__":
    unittest.main()
