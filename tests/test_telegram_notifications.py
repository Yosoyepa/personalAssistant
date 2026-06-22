from __future__ import annotations

import unittest

from personal_assistant.adapters.outbound.notifications.telegram import TelegramNotificationTool
from personal_assistant.application.ports.notifications import NotificationRequest
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_message(self, *, chat_id: str, text: str) -> dict[str, int]:
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.sent)}


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


if __name__ == "__main__":
    unittest.main()
