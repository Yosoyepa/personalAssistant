from __future__ import annotations

from datetime import UTC, datetime
import unittest

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage
from personal_assistant.application.dto.commands import CommandKind
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.adapters.inbound.api import normalize_telegram_webhook
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container


class CommandRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = build_container()
        self.principal = Principal.for_test(
            principal_id="telegram-user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    def message(self, text: str, message_id: str = "42") -> NormalizedMessage:
        return NormalizedMessage(
            channel=ChannelName.telegram,
            actor_id=self.principal.principal_id,
            conversation_id="chat-1",
            message_id=message_id,
            text=text,
        )

    def test_help_command_lists_approval_commands(self) -> None:
        result = self.container.commands.handle(
            self.principal,
            self.message("/help"),
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertEqual(result.kind, CommandKind.help)
        self.assertIn("/aprobar <id>", result.reply)
        self.assertIn("/cancelar <id>", result.reply)

    def test_telegram_normalized_bot_command_routes_to_reminder(self) -> None:
        normalized = normalize_telegram_webhook(
            {
                "update_id": 101,
                "message": {
                    "message_id": 42,
                    "chat": {"id": "chat-1"},
                    "from": {"id": self.principal.principal_id},
                    "text": "/recordar@personal_bot recuérdame clase el martes a las 5",
                },
            },
            tenant_id=self.principal.tenant_id,
        )

        result = self.container.commands.handle(
            self.principal,
            normalized,
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(normalized.command, "recordar")
        self.assertEqual(normalized.command_args, "recuérdame clase el martes a las 5")
        self.assertEqual(normalized.idempotency_key, "telegram:101")
        self.assertEqual(result.status, AgentStatus.escalated)
        self.assertEqual(len(self.container.approvals.list_pending(self.principal)), 1)

    def test_reminder_command_creates_pending_approval_without_side_effect(self) -> None:
        result = self.container.commands.handle(
            self.principal,
            self.message("/recordar recuérdame clase el martes a las 5"),
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(result.status, AgentStatus.escalated)
        self.assertEqual(result.kind, CommandKind.reminder_create)
        self.assertIsNotNone(result.approval_id)
        self.assertEqual(len(self.container.approvals.list_pending(self.principal)), 1)
        self.assertEqual(self.container.calendar.list_events(self.principal), [])

    def test_approve_pending_reminder_creates_calendar_event_and_agenda(self) -> None:
        pending = self.container.commands.handle(
            self.principal,
            self.message("recuérdame clase el martes a las 5"),
            now=self.now,
            timezone="America/Bogota",
        )
        assert pending.approval_id is not None

        approved = self.container.commands.handle(
            self.principal,
            self.message(f"/aprobar {pending.approval_id}", message_id="43"),
            now=self.now,
            timezone="America/Bogota",
        )
        agenda = self.container.commands.handle(
            self.principal,
            self.message("/agenda", message_id="44"),
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(approved.status, AgentStatus.completed)
        self.assertEqual(len(self.container.calendar.list_events(self.principal)), 1)
        self.assertIn("clase", agenda.reply)
        self.assertEqual(self.container.approvals.list_pending(self.principal), [])

    def test_cancel_pending_approval_blocks_later_approval(self) -> None:
        pending = self.container.commands.handle(
            self.principal,
            self.message("recuérdame clase el martes a las 5"),
            now=self.now,
            timezone="America/Bogota",
        )
        assert pending.approval_id is not None

        cancelled = self.container.commands.handle(
            self.principal,
            self.message(f"/cancelar {pending.approval_id}", message_id="43"),
            now=self.now,
            timezone="America/Bogota",
        )
        approved = self.container.commands.handle(
            self.principal,
            self.message(f"/aprobar {pending.approval_id}", message_id="44"),
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(cancelled.status, AgentStatus.completed)
        self.assertEqual(approved.status, AgentStatus.failed)
        self.assertEqual(self.container.calendar.list_events(self.principal), [])

    def test_other_principal_cannot_approve_pending_request(self) -> None:
        pending = self.container.commands.handle(
            self.principal,
            self.message("recuérdame clase el martes a las 5"),
            now=self.now,
            timezone="America/Bogota",
        )
        assert pending.approval_id is not None
        other = Principal.for_test(
            principal_id="telegram-user-2",
            tenant_id=self.principal.tenant_id,
            permission_tier=PermissionTier.P5,
        )

        result = self.container.commands.handle(
            other,
            NormalizedMessage(
                channel=ChannelName.telegram,
                actor_id=other.principal_id,
                conversation_id="chat-2",
                message_id="43",
                text=f"/aprobar {pending.approval_id}",
                command="aprobar",
                command_args=pending.approval_id,
            ),
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(result.status, AgentStatus.failed)
        self.assertEqual(self.container.calendar.list_events(self.principal), [])

    def test_status_summarizes_local_runtime_counts(self) -> None:
        result = self.container.commands.handle(
            self.principal,
            self.message("/status"),
            now=self.now,
            timezone="America/Bogota",
        )

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertIn("Estado local: activo", result.reply)
        self.assertIn("Pendientes: 0", result.reply)


if __name__ == "__main__":
    unittest.main()
