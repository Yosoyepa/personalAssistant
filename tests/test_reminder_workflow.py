from __future__ import annotations

from datetime import UTC, datetime
import unittest

from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.calendar.local import LocalCalendarTool
from personal_assistant.notifications.local import LocalNotificationTool
from personal_assistant.reminders.models import ReminderWorkflowInput
from personal_assistant.reminders.workflow import ReminderWorkflow, extract_reminder, reminder_idempotency_key
from personal_assistant.scheduler.service import ReminderScheduler
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.tracing import TraceEventType, TraceRecorder
from personal_assistant.stores.in_memory import InMemoryEventStore, InMemoryOutbox, InMemoryWorkflowStateStore


class ReminderWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.calendar = LocalCalendarTool()
        self.notifications = LocalNotificationTool()
        self.scheduler = ReminderScheduler(self.notifications)
        self.event_store = InMemoryEventStore()
        self.outbox = InMemoryOutbox()
        self.states = InMemoryWorkflowStateStore()
        self.traces = TraceRecorder()
        self.workflow = ReminderWorkflow(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            traces=self.traces,
        )

    def request(self, approval: ApprovalGrant | None = None) -> ReminderWorkflowInput:
        text = "recuérdame clase el martes a las 5"
        key = reminder_idempotency_key(self.principal.tenant_id, "42", text)
        if approval is None:
            approval = ApprovalGrant.issue(
                principal=self.principal,
                action="calendar.create_event",
                resource=f"{key}:calendar",
                tier=PermissionTier.P3,
            )
        return ReminderWorkflowInput(
            message_id="42",
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=datetime(2026, 6, 20, 12, tzinfo=UTC),
            idempotency_key=key,
            approval=approval,
        )

    def request_without_approval(self) -> ReminderWorkflowInput:
        text = "recuérdame clase el martes a las 5"
        return ReminderWorkflowInput(
            message_id="42",
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=datetime(2026, 6, 20, 12, tzinfo=UTC),
            idempotency_key=reminder_idempotency_key(self.principal.tenant_id, "42", text),
            approval=None,
        )

    def test_extract_reminder_parses_weekday_and_hour(self) -> None:
        extraction = extract_reminder("recuérdame clase el martes a las 5", datetime(2026, 6, 20, 12, tzinfo=UTC))

        self.assertIsNotNone(extraction)
        assert extraction is not None
        self.assertEqual(extraction.starts_at.weekday(), 1)
        self.assertEqual(extraction.starts_at.hour, 5)

    def test_happy_path_creates_calendar_event_and_schedules_notice(self) -> None:
        result = self.workflow.run(self.principal, self.request())

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertIsNotNone(result.calendar_event_id)
        self.assertIsNotNone(result.reminder_id)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)
        self.assertEqual(len(self.scheduler.due(self.principal, datetime(2026, 6, 23, 16, 31, tzinfo=UTC))), 1)
        self.assertEqual(len(self.event_store.list_for_tenant(self.principal)), 1)
        self.assertEqual(len(self.outbox.claim(self.principal)), 1)
        trace_types = [event.event_type for event in self.traces.list_for_tenant(self.principal.tenant_id)]
        self.assertIn(TraceEventType.agent_started, trace_types)
        self.assertIn(TraceEventType.guardrail_checked, trace_types)
        self.assertIn(TraceEventType.context_selected, trace_types)
        self.assertIn(TraceEventType.tool_called, trace_types)
        self.assertIn(TraceEventType.agent_completed, trace_types)

    def test_duplicate_webhook_reuses_completed_state(self) -> None:
        first = self.workflow.run(self.principal, self.request())
        second = self.workflow.run(self.principal, self.request())

        self.assertEqual(first.calendar_event_id, second.calendar_event_id)
        self.assertEqual(first.reminder_id, second.reminder_id)
        self.assertTrue(second.reused)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)
        self.assertEqual(len(self.event_store.list_for_tenant(self.principal)), 1)

    def test_missing_approval_does_not_create_side_effect(self) -> None:
        result = self.workflow.run(self.principal, self.request_without_approval())

        self.assertEqual(result.status, AgentStatus.escalated)
        self.assertTrue(result.approval_required)
        self.assertEqual(self.calendar.list_events(self.principal), [])
        self.assertEqual(self.event_store.list_for_tenant(self.principal), [])
        self.assertEqual(self.outbox.claim(self.principal), [])

    def test_idempotency_key_is_derived_when_missing(self) -> None:
        text = "recuérdame clase el martes a las 5"
        key = reminder_idempotency_key(self.principal.tenant_id, "42", text)
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="calendar.create_event",
            resource=f"{key}:calendar",
            tier=PermissionTier.P3,
        )
        request = ReminderWorkflowInput(
            message_id="42",
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=datetime(2026, 6, 20, 12, tzinfo=UTC),
            approval=approval,
        )

        first = self.workflow.run(self.principal, request)
        second = self.workflow.run(self.principal, request)

        self.assertEqual(first.calendar_event_id, second.calendar_event_id)
        self.assertTrue(second.reused)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)

    def test_same_weekday_later_today_is_not_skipped(self) -> None:
        extraction = extract_reminder("recuérdame clase el martes a las 17", datetime(2026, 6, 16, 9, tzinfo=UTC))

        self.assertIsNotNone(extraction)
        assert extraction is not None
        self.assertEqual(extraction.starts_at.date().isoformat(), "2026-06-16")
        self.assertEqual(extraction.starts_at.hour, 17)

    def test_pm_and_invalid_minutes_are_handled(self) -> None:
        extraction = extract_reminder("recuérdame clase el martes a las 9 pm", datetime(2026, 6, 20, 12, tzinfo=UTC))
        invalid = extract_reminder("recuérdame clase el martes a las 5:99", datetime(2026, 6, 20, 12, tzinfo=UTC))

        self.assertIsNotNone(extraction)
        assert extraction is not None
        self.assertEqual(extraction.starts_at.hour, 21)
        self.assertIsNone(invalid)


if __name__ == "__main__":
    unittest.main()
