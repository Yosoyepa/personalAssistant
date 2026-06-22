from __future__ import annotations

from datetime import UTC, datetime
import json
import unittest

from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.memory.models import MemoryKind
from personal_assistant.infrastructure.admin import AdminDashboard, is_local_client
from personal_assistant.infrastructure.bootstrap import build_container


class AdminDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = build_container()
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.dashboard = AdminDashboard(self.container)

    def request(
        self,
        principal: Principal,
        *,
        message_id: str = "42",
        text: str = "recuerdame clase el martes a las 5",
        approved: bool = True,
    ) -> ReminderWorkflowInput:
        key = reminder_idempotency_key(principal.tenant_id, message_id, text)
        approval = None
        if approved:
            approval = ApprovalGrant.issue(
                principal=principal,
                action="calendar.create_event",
                resource=f"{key}:calendar",
                tier=PermissionTier.P3,
            )
        return ReminderWorkflowInput(
            message_id=message_id,
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=datetime(2026, 6, 20, 12, tzinfo=UTC),
            idempotency_key=key,
            approval=approval,
        )

    def test_snapshot_surfaces_waiting_approval_and_trace_health(self) -> None:
        self.container.reminder_workflow.run(
            self.principal,
            self.request(self.principal, approved=False),
        )

        snapshot = self.dashboard.snapshot(
            self.principal,
            now=datetime(2026, 6, 23, 16, 31, tzinfo=UTC),
        )

        self.assertEqual(snapshot["health"]["status"], "needs_attention")
        self.assertEqual(snapshot["health"]["attention"]["pending_approvals"], 1)
        self.assertEqual(snapshot["approvals"]["pending_count"], 1)
        self.assertEqual(snapshot["states"]["counts"]["waiting_approval"], 1)
        self.assertEqual(snapshot["traces"]["counts"]["approval.requested"], 1)
        self.assertEqual(snapshot["events"]["total"], 0)
        self.assertEqual(snapshot["outbox"]["total"], 0)
        self.assertEqual(snapshot["scheduler"]["total"], 0)

    def test_completed_run_exposes_outbox_scheduler_events_and_html_structure(self) -> None:
        self.container.reminder_workflow.run(
            self.principal,
            self.request(self.principal),
        )

        snapshot = self.dashboard.snapshot(
            self.principal,
            now=datetime(2026, 6, 23, 16, 31, tzinfo=UTC),
        )
        html = self.dashboard.render_html(
            self.principal,
            now=datetime(2026, 6, 23, 16, 31, tzinfo=UTC),
        )

        self.assertEqual(snapshot["events"]["counts"]["reminder.created"], 1)
        self.assertEqual(snapshot["outbox"]["counts"]["pending"], 1)
        self.assertEqual(snapshot["scheduler"]["counts"]["due"], 1)
        self.assertEqual(snapshot["states"]["counts"]["completed"], 1)
        self.assertEqual(snapshot["memory"]["total"], 0)
        for section in ("health", "approvals", "traces", "outbox", "scheduler", "events", "states", "memory"):
            self.assertIn(f'<section id="{section}">', html)
        self.assertIn("Personal Assistant Admin", html)
        self.assertIn("reminder.created", html)

    def test_snapshot_is_tenant_and_actor_scoped(self) -> None:
        tenant_b = Principal.for_test(
            principal_id="user-2",
            tenant_id="tenant-b",
            permission_tier=PermissionTier.P5,
        )
        self.container.memory.add(
            self.principal,
            kind=MemoryKind.semantic,
            text="Project Alpha ships in July",
            source="test",
            confirmed=True,
        )
        self.container.memory.add(
            tenant_b,
            kind=MemoryKind.semantic,
            text="Project Beta tenant-b secret",
            source="test",
            confirmed=True,
        )
        self.container.reminder_workflow.run(
            tenant_b,
            self.request(tenant_b, message_id="99", text="recuerdame tenant-b secret el martes a las 5"),
        )

        snapshot = self.dashboard.snapshot(self.principal)
        payload = json.dumps(snapshot, sort_keys=True)

        self.assertIn("Project Alpha", payload)
        self.assertNotIn("Project Beta", payload)
        self.assertNotIn("tenant-b", payload)

    def test_local_client_guard_allows_only_loopback(self) -> None:
        self.assertTrue(is_local_client("127.0.0.1"))
        self.assertTrue(is_local_client("127.2.3.4:8000"))
        self.assertTrue(is_local_client("::1"))
        self.assertTrue(is_local_client("[::1]:8000"))
        self.assertTrue(is_local_client("localhost"))
        self.assertFalse(is_local_client("192.168.1.10"))
        self.assertFalse(is_local_client("10.0.0.2:8000"))
        self.assertFalse(is_local_client("example.com"))
        self.assertFalse(is_local_client(None))


if __name__ == "__main__":
    unittest.main()
