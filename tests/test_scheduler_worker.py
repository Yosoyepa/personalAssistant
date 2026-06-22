from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from personal_assistant.adapters.outbound.notifications.local import LocalNotificationTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.application.use_cases.reminder_notifications import DispatchDueReminders
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.worker import ReminderWorker, RuntimeNotificationApprovalPolicy


class SchedulerWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.scheduler = ReminderScheduler()
        self.notifications = LocalNotificationTool()
        self.dispatcher = DispatchDueReminders(scheduler=self.scheduler, notifications=self.notifications)

    def worker(self, *, approve_notifications: bool = True) -> ReminderWorker:
        return ReminderWorker(
            dispatcher=self.dispatcher,
            approval_policy=RuntimeNotificationApprovalPolicy(approve_notifications=approve_notifications),
            clock=lambda: self.now,
            sleep=lambda _: None,
        )

    def schedule_due(self, principal: Principal, key: str, *, minutes_from_now: int = 1) -> str:
        job = self.scheduler.schedule_before_event(
            principal,
            calendar_event_id=f"cal-{key}",
            starts_at=self.now + timedelta(minutes=minutes_from_now),
            channel="telegram",
            recipient="chat-1",
            body=f"Recordatorio {key}",
            minutes_before=30,
            idempotency_key=key,
        )
        return job.reminder_id

    def test_run_once_dispatches_due_reminder_with_runtime_p5_approval(self) -> None:
        self.schedule_due(self.principal, "notify-1")

        tick = self.worker().run_once(self.principal, now=self.now)

        self.assertEqual(tick.due_count, 1)
        self.assertEqual(tick.sent_count, 1)
        self.assertEqual(tick.skipped_count, 0)
        self.assertEqual(self.scheduler.due(self.principal, self.now), [])
        sent = self.notifications.list_sent(self.principal)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].idempotency_key, "notify-1")

    def test_run_once_skips_without_runtime_approval_and_leaves_job_due(self) -> None:
        reminder_id = self.schedule_due(self.principal, "notify-denied")

        tick = self.worker(approve_notifications=False).run_once(self.principal, now=self.now)

        self.assertEqual(tick.due_count, 1)
        self.assertEqual(tick.sent_count, 0)
        self.assertEqual(tick.skipped_reminder_ids, (reminder_id,))
        self.assertEqual(len(self.scheduler.due(self.principal, self.now)), 1)
        self.assertEqual(self.notifications.list_sent(self.principal), [])

    def test_run_once_issues_one_approval_per_due_reminder(self) -> None:
        self.schedule_due(self.principal, "notify-a")
        self.schedule_due(self.principal, "notify-b", minutes_from_now=2)

        tick = self.worker().run_once(self.principal, now=self.now)

        self.assertEqual(tick.due_count, 2)
        self.assertEqual(tick.sent_count, 2)
        sent_keys = {notification.idempotency_key for notification in self.notifications.list_sent(self.principal)}
        self.assertEqual(sent_keys, {"notify-a", "notify-b"})

    def test_principal_below_p5_cannot_dispatch_notification(self) -> None:
        low_tier = Principal.for_test(
            principal_id="user-low",
            tenant_id="tenant-low",
            permission_tier=PermissionTier.P3,
        )
        self.schedule_due(low_tier, "notify-low")

        with self.assertRaises(AssistantError) as ctx:
            self.worker().run_once(low_tier, now=self.now)

        self.assertEqual(ctx.exception.code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(len(self.scheduler.due(low_tier, self.now)), 1)
        self.assertEqual(self.notifications.list_sent(low_tier), [])

    def test_worker_dispatch_is_tenant_scoped(self) -> None:
        tenant_b = Principal.for_test(
            principal_id="user-2",
            tenant_id="tenant-b",
            permission_tier=PermissionTier.P5,
        )
        self.schedule_due(self.principal, "notify-tenant-a")
        self.schedule_due(tenant_b, "notify-tenant-b")

        tick = self.worker().run_once(self.principal, now=self.now)

        self.assertEqual(tick.sent_count, 1)
        self.assertEqual(len(self.notifications.list_sent(self.principal)), 1)
        self.assertEqual(self.notifications.list_sent(tenant_b), [])
        self.assertEqual(len(self.scheduler.due(tenant_b, self.now)), 1)

    def test_run_loop_can_be_bounded_for_manual_invocation(self) -> None:
        sleep_calls: list[float] = []
        self.schedule_due(self.principal, "notify-loop")
        worker = ReminderWorker(
            dispatcher=self.dispatcher,
            approval_policy=RuntimeNotificationApprovalPolicy(approve_notifications=True),
            clock=lambda: self.now,
            sleep=sleep_calls.append,
        )

        ticks = worker.run_loop(self.principal, interval_seconds=0.25, max_ticks=2)

        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks[0].sent_count, 1)
        self.assertEqual(ticks[1].due_count, 0)
        self.assertEqual(sleep_calls, [0.25])
        self.assertEqual(len(self.notifications.list_sent(self.principal)), 1)


if __name__ == "__main__":
    unittest.main()
