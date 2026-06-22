from __future__ import annotations

from datetime import UTC, datetime
import unittest

from pydantic import ValidationError

from personal_assistant.application.ports.calendar import CalendarEventRequest
from personal_assistant.application.ports.notifications import NotificationRequest
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.persistence.memory import TenantMemoryStore
from personal_assistant.adapters.outbound.notifications.local import LocalNotificationTool
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.memory.models import MemoryKind


class PermissionAndTenantTests(unittest.TestCase):
    def principal(self, tenant_id: str, tier: PermissionTier = PermissionTier.P5) -> Principal:
        return Principal.for_test(principal_id=f"user-{tenant_id}", tenant_id=tenant_id, permission_tier=tier)

    def test_principal_requires_tenant_id(self) -> None:
        with self.assertRaises(ValidationError):
            Principal(principal_id="user", auth_subject="user", permission_tier=PermissionTier.P0)

    def test_calendar_requires_approval_for_p3_write(self) -> None:
        tool = LocalCalendarTool()
        principal = self.principal("tenant-a", PermissionTier.P3)
        request = CalendarEventRequest(
            title="Clase",
            starts_at=datetime(2026, 6, 23, 17, tzinfo=UTC),
            idempotency_key="cal-1",
        )

        with self.assertRaises(AssistantError) as ctx:
            tool.create_event(principal, request)

        self.assertEqual(ctx.exception.code, ErrorCode.PERMISSION_DENIED)
        approval = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource="cal-1",
            tier=PermissionTier.P3,
        )
        created = tool.create_event(principal, request, approval=approval)
        with self.assertRaises(AssistantError):
            tool.create_event(principal, request)
        reused = tool.create_event(principal, request, approval=approval)
        self.assertEqual(created.event_id, reused.event_id)
        self.assertTrue(reused.reused)

    def test_notification_requires_approval_for_p5_send(self) -> None:
        tool = LocalNotificationTool()
        principal = self.principal("tenant-a", PermissionTier.P5)
        request = NotificationRequest(
            channel="telegram",
            recipient="123",
            body="Recordatorio",
            idempotency_key="msg-1",
        )

        with self.assertRaises(AssistantError) as ctx:
            tool.send(principal, request)

        self.assertEqual(ctx.exception.code, ErrorCode.PERMISSION_DENIED)
        approval = ApprovalGrant.issue(
            principal=principal,
            action="notification.send",
            resource="msg-1",
            tier=PermissionTier.P5,
        )
        sent = tool.send(principal, request, approval=approval)
        with self.assertRaises(AssistantError):
            tool.send(principal, request)
        reused = tool.send(principal, request, approval=approval)
        self.assertEqual(sent.notification_id, reused.notification_id)
        self.assertTrue(reused.reused)

    def test_untrusted_principal_cannot_read_adapter_state(self) -> None:
        calendar = LocalCalendarTool()
        notifications = LocalNotificationTool()
        principal = self.principal("tenant-a", PermissionTier.P5)
        untrusted = Principal(
            principal_id="forged",
            tenant_id="tenant-a",
            auth_subject="forged",
            permission_tier=PermissionTier.P5,
        )

        calendar_request = CalendarEventRequest(
            title="Clase",
            starts_at=datetime(2026, 6, 23, 17, tzinfo=UTC),
            idempotency_key="cal-read",
        )
        notification_request = NotificationRequest(
            channel="telegram",
            recipient="123",
            body="Recordatorio",
            idempotency_key="msg-read",
        )
        calendar.create_event(
            principal,
            calendar_request,
            approval=ApprovalGrant.issue(
                principal=principal,
                action="calendar.create_event",
                resource="cal-read",
                tier=PermissionTier.P3,
            ),
        )
        notifications.send(
            principal,
            notification_request,
            approval=ApprovalGrant.issue(
                principal=principal,
                action="notification.send",
                resource="msg-read",
                tier=PermissionTier.P5,
            ),
        )

        with self.assertRaises(AssistantError) as calendar_ctx:
            calendar.list_events(untrusted)
        with self.assertRaises(AssistantError) as notification_ctx:
            notifications.list_sent(untrusted)

        self.assertEqual(calendar_ctx.exception.code, ErrorCode.AUTHENTICATION_REQUIRED)
        self.assertEqual(notification_ctx.exception.code, ErrorCode.AUTHENTICATION_REQUIRED)

    def test_calendar_and_notification_idempotency_conflicts_are_not_silent(self) -> None:
        calendar = LocalCalendarTool()
        notifications = LocalNotificationTool()
        principal = self.principal("tenant-a", PermissionTier.P5)
        calendar_approval = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource="same-calendar-key",
            tier=PermissionTier.P3,
        )
        notification_approval = ApprovalGrant.issue(
            principal=principal,
            action="notification.send",
            resource="same-notification-key",
            tier=PermissionTier.P5,
        )

        calendar.create_event(
            principal,
            CalendarEventRequest(
                title="Clase",
                starts_at=datetime(2026, 6, 23, 17, tzinfo=UTC),
                idempotency_key="same-calendar-key",
            ),
            approval=calendar_approval,
        )
        notifications.send(
            principal,
            NotificationRequest(
                channel="telegram",
                recipient="123",
                body="Recordatorio",
                idempotency_key="same-notification-key",
            ),
            approval=notification_approval,
        )

        with self.assertRaises(AssistantError) as calendar_ctx:
            calendar.create_event(
                principal,
                CalendarEventRequest(
                    title="Otro titulo",
                    starts_at=datetime(2026, 6, 23, 17, tzinfo=UTC),
                    idempotency_key="same-calendar-key",
                ),
                approval=calendar_approval,
            )
        with self.assertRaises(AssistantError) as notification_ctx:
            notifications.send(
                principal,
                NotificationRequest(
                    channel="telegram",
                    recipient="123",
                    body="Otro cuerpo",
                    idempotency_key="same-notification-key",
                ),
                approval=notification_approval,
            )

        self.assertEqual(calendar_ctx.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(notification_ctx.exception.code, ErrorCode.CONFLICT)

    def test_memory_retrieval_is_tenant_scoped(self) -> None:
        store = TenantMemoryStore()
        tenant_a = self.principal("tenant-a")
        tenant_b = self.principal("tenant-b")
        store.add(
            tenant_a,
            kind=MemoryKind.semantic,
            text="Project Zephyr ships 2026-09-01",
            source="test",
            confirmed=True,
        )

        self.assertEqual(store.retrieve(tenant_b, query="Project Zephyr"), [])
        self.assertEqual(len(store.retrieve(tenant_a, query="Project Zephyr")), 1)

    def test_calendar_events_are_tenant_scoped(self) -> None:
        tool = LocalCalendarTool()
        tenant_a = self.principal("tenant-a", PermissionTier.P3)
        tenant_b = self.principal("tenant-b", PermissionTier.P3)
        request = CalendarEventRequest(
            title="Project Zephyr ships 2026-09-01",
            starts_at=datetime(2026, 6, 23, 17, tzinfo=UTC),
            idempotency_key="shared-key",
        )
        approval = ApprovalGrant.issue(
            principal=tenant_a,
            action="calendar.create_event",
            resource="shared-key",
            tier=PermissionTier.P3,
        )

        tool.create_event(tenant_a, request, approval=approval)

        self.assertEqual(tool.list_events(tenant_b), [])
        self.assertEqual(len(tool.list_events(tenant_a)), 1)


if __name__ == "__main__":
    unittest.main()
