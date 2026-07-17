from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace
import unittest

from personal_assistant.application.dto.events import (
    CloudEvent,
    OutboxMessage,
    OutboxStatus,
)
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.calendar import CalendarEventResult
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.memory.models import MemoryKind
from personal_assistant.infrastructure.admin import AdminDashboard, is_local_client
from personal_assistant.infrastructure.bootstrap import build_container


class PublicOnlyTenantListAdapter:
    def __init__(self, items: list[object]) -> None:
        object.__setattr__(self, "_items", items)
        object.__setattr__(self, "seen_principals", [])

    def __getattribute__(self, name: str) -> object:
        if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
            raise AssertionError(
                f"admin dashboard read private adapter attribute {name}"
            )
        return object.__getattribute__(self, name)

    def list_for_tenant(self, principal: Principal) -> list[object]:
        object.__getattribute__(self, "seen_principals").append(principal)
        return [
            item
            for item in object.__getattribute__(self, "_items")
            if getattr(item, "tenant_id") == principal.tenant_id
        ]


class PublicOnlyCalendarAdapter:
    def __init__(self, events: list[CalendarEventResult]) -> None:
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "seen_principals", [])

    def __getattribute__(self, name: str) -> object:
        if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
            raise AssertionError(
                f"admin dashboard read private calendar attribute {name}"
            )
        return object.__getattribute__(self, name)

    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        object.__getattribute__(self, "seen_principals").append(principal)
        return list(object.__getattribute__(self, "_events"))


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
        text: str = "recuerdame clase el martes a las 11 am",
        approved: bool = True,
    ) -> ReminderWorkflowInput:
        key = reminder_idempotency_key(
            tenant_id=principal.tenant_id,
            channel="telegram",
            principal_id=principal.principal_id,
            conversation_id="chat-1",
            source_event_id=message_id,
        )
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
            source_event_id=message_id,
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

    def test_completed_run_exposes_outbox_scheduler_events_and_html_structure(
        self,
    ) -> None:
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
        self.assertEqual(snapshot["agenda"]["total"], 1)
        self.assertEqual(snapshot["reminders"]["counts"]["due"], 1)
        self.assertEqual(snapshot["errors"]["total"], 0)
        for section in (
            "health",
            "agenda",
            "reminders",
            "errors",
            "approvals",
            "traces",
            "outbox",
            "scheduler",
            "events",
            "states",
            "memory",
        ):
            self.assertIn(f'<section id="{section}">', html)
        self.assertIn("Personal Assistant Admin", html)
        self.assertIn("reminder.created", html)

    def test_snapshot_surfaces_agenda_reminders_and_errors(self) -> None:
        now = datetime(2026, 6, 23, 16, 31, tzinfo=UTC)
        failed_event = CloudEvent(
            id="evt-failed-reminder",
            type="reminder.dispatch_failed",
            source="test",
            subject="rem-failed",
            tenant_id="tenant-a",
            data={"error": "telegram unavailable"},
            time=datetime(2026, 6, 23, 16, 20, tzinfo=UTC),
        )
        failed_message = OutboxMessage(
            id="outbox-failed",
            tenant_id="tenant-a",
            event=failed_event,
            idempotency_key="outbox-failed",
            dispatch_status=OutboxStatus.failed,
            attempts=3,
            created_at=datetime(2026, 6, 23, 16, 20, tzinfo=UTC),
        )
        due_job = ScheduledReminder(
            reminder_id="rem-due",
            tenant_id="tenant-a",
            calendar_event_id="cal-due",
            notify_at=datetime(2026, 6, 23, 16, 5, tzinfo=UTC),
            channel="telegram",
            recipient="chat-1",
            body="due reminder",
            idempotency_key="due-reminder",
            timezone="America/Bogota",
            source_event_id="event-due",
            payload_fingerprint="a" * 64,
        )
        pending_job = ScheduledReminder(
            reminder_id="rem-pending",
            tenant_id="tenant-a",
            calendar_event_id="cal-pending",
            notify_at=datetime(2026, 6, 23, 17, 30, tzinfo=UTC),
            channel="telegram",
            recipient="chat-1",
            body="pending reminder",
            idempotency_key="pending-reminder",
            timezone="America/Bogota",
            source_event_id="event-pending",
            payload_fingerprint="b" * 64,
        )
        sent_job = ScheduledReminder(
            reminder_id="rem-sent",
            tenant_id="tenant-a",
            calendar_event_id="cal-sent",
            notify_at=datetime(2026, 6, 23, 15, 30, tzinfo=UTC),
            channel="telegram",
            recipient="chat-1",
            body="sent reminder",
            idempotency_key="sent-reminder",
            timezone="America/Bogota",
            source_event_id="event-sent",
            payload_fingerprint="c" * 64,
            sent=True,
        )
        agenda_events = [
            CalendarEventResult(
                event_id="cal-due",
                title="Due reminder event",
                starts_at=datetime(2026, 6, 23, 16, 35, tzinfo=UTC),
                idempotency_key="cal-due",
                timezone="America/Bogota",
                source_event_id="event-due",
                payload_fingerprint="a" * 64,
            ),
            CalendarEventResult(
                event_id="cal-pending",
                title="Pending reminder event",
                starts_at=datetime(2026, 6, 23, 18, 0, tzinfo=UTC),
                idempotency_key="cal-pending",
                timezone="America/Bogota",
                source_event_id="event-pending",
                payload_fingerprint="b" * 64,
            ),
            CalendarEventResult(
                event_id="cal-past",
                title="Past agenda event",
                starts_at=datetime(2026, 6, 23, 15, 0, tzinfo=UTC),
                idempotency_key="cal-past",
                timezone="America/Bogota",
                source_event_id="event-past",
                payload_fingerprint="d" * 64,
            ),
        ]
        failed_state = WorkflowState(
            workflow_id="wf-failed",
            tenant_id="tenant-a",
            workflow_type="reminder.dispatch",
            status=WorkflowStatus.failed,
            step="send_notification",
            idempotency_key="wf-failed",
            data={"error": "telegram unavailable"},
            created_at=datetime(2026, 6, 23, 16, 0, tzinfo=UTC),
            updated_at=datetime(2026, 6, 23, 16, 21, tzinfo=UTC),
        )
        failed_trace = TraceEvent(
            trace_id="trace-failed",
            run_id="run-failed",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id="tenant-a",
            timestamp=datetime(2026, 6, 23, 16, 22, tzinfo=UTC),
            error={"type": "RuntimeError", "message": "telegram unavailable"},
        )
        dashboard = AdminDashboard(
            SimpleNamespace(
                traces=PublicOnlyTenantListAdapter([failed_trace]),
                outbox=PublicOnlyTenantListAdapter([failed_message]),
                scheduler=PublicOnlyTenantListAdapter([due_job, pending_job, sent_job]),
                calendar=PublicOnlyCalendarAdapter(agenda_events),
                event_store=PublicOnlyTenantListAdapter([failed_event]),
                states=PublicOnlyTenantListAdapter([failed_state]),
                memory=PublicOnlyTenantListAdapter([]),
            )
        )

        snapshot = dashboard.snapshot(self.principal, now=now)

        self.assertEqual(snapshot["health"]["status"], "needs_attention")
        self.assertEqual(snapshot["health"]["attention"]["due_reminders"], 1)
        self.assertEqual(snapshot["health"]["attention"]["errors"], 3)
        self.assertEqual(snapshot["health"]["attention"]["failed_outbox"], 1)
        self.assertEqual(snapshot["health"]["attention"]["failed_workflows"], 1)
        self.assertEqual(snapshot["agenda"]["total"], 3)
        self.assertEqual(snapshot["agenda"]["upcoming_count"], 2)
        self.assertEqual(snapshot["agenda"]["today_count"], 3)
        self.assertEqual(snapshot["agenda"]["past_count"], 1)
        self.assertEqual(
            snapshot["scheduler"]["counts"],
            {"scheduled": 3, "due": 1, "sent": 1, "pending": 1},
        )
        self.assertEqual(
            snapshot["reminders"]["counts"],
            {"scheduled": 3, "due": 1, "sent": 1, "pending": 1},
        )
        scheduler_by_id = {
            item["idempotency_key"]: item for item in snapshot["scheduler"]["items"]
        }
        reminders_by_id = {
            item["idempotency_key"]: item for item in snapshot["reminders"]["items"]
        }
        self.assertEqual(scheduler_by_id["due-reminder"]["status"], "due")
        self.assertTrue(scheduler_by_id["due-reminder"]["due"])
        self.assertEqual(
            reminders_by_id["due-reminder"]["event_title"], "Due reminder event"
        )
        self.assertEqual(
            scheduler_by_id[pending_job.idempotency_key]["status"], "scheduled"
        )
        self.assertEqual(scheduler_by_id["sent-reminder"]["status"], "sent")
        self.assertEqual(snapshot["outbox"]["counts"]["failed"], 1)
        self.assertEqual(snapshot["events"]["counts"]["reminder.dispatch_failed"], 1)
        self.assertEqual(snapshot["states"]["counts"]["failed"], 1)
        self.assertEqual(snapshot["traces"]["counts"]["agent.failed"], 1)
        self.assertEqual(
            snapshot["traces"]["items"][0]["error"]["message"], "telegram unavailable"
        )
        self.assertEqual(snapshot["errors"]["total"], 3)
        self.assertEqual(
            snapshot["errors"]["counts"], {"trace": 1, "workflow": 1, "outbox": 1}
        )
        self.assertEqual(snapshot["errors"]["event_type_counts"]["agent.failed"], 1)
        self.assertIn(
            "telegram unavailable",
            {item["message"] for item in snapshot["errors"]["items"]},
        )

    def test_trace_errors_are_categorized_filterable_and_grouped_by_run(self) -> None:
        events = [
            TraceEvent(
                trace_id="trace-audio",
                run_id="run-audio",
                agent_id="personal_assistant",
                event_type=TraceEventType.agent_failed,
                tenant_id="tenant-a",
                timestamp=datetime(2026, 6, 23, 16, 1, tzinfo=UTC),
                input_summary={"media_kind": "voice", "media_mime_type": "audio/ogg"},
                error={
                    "type": "TranscriptionError",
                    "message": "unsupported audio format",
                },
            ),
            TraceEvent(
                trace_id="trace-llm",
                run_id="run-shared",
                agent_id="personal_assistant",
                event_type=TraceEventType.llm_called,
                tenant_id="tenant-a",
                timestamp=datetime(2026, 6, 23, 16, 2, tzinfo=UTC),
                model="configured",
                input_summary={"schema": "reminder_extraction"},
                error={"type": "TimeoutError", "message": "model timeout"},
            ),
            TraceEvent(
                trace_id="trace-tool",
                run_id="run-shared",
                agent_id="personal_assistant",
                event_type=TraceEventType.tool_called,
                tenant_id="tenant-a",
                timestamp=datetime(2026, 6, 23, 16, 3, tzinfo=UTC),
                tool_call={"name": "calendar.create_event"},
                error={"type": "ToolError", "message": "calendar rejected event"},
            ),
            TraceEvent(
                trace_id="trace-workflow",
                run_id="workflow:reminder-worker",
                agent_id="personal_assistant",
                event_type=TraceEventType.agent_failed,
                tenant_id="tenant-a",
                timestamp=datetime(2026, 6, 23, 16, 4, tzinfo=UTC),
                error={"type": "RuntimeError", "message": "worker loop failed"},
            ),
            TraceEvent(
                trace_id="trace-other-tenant",
                run_id="run-audio",
                agent_id="personal_assistant",
                event_type=TraceEventType.agent_failed,
                tenant_id="tenant-b",
                timestamp=datetime(2026, 6, 23, 16, 5, tzinfo=UTC),
                input_summary={"media_kind": "voice"},
                error={"type": "TranscriptionError", "message": "tenant-b audio"},
            ),
        ]
        for event in events:
            self.container.traces.write(event)

        errors = self.dashboard.errors(self.principal)
        audio_errors = self.dashboard.errors(self.principal, category="audio")
        shared_run = self.dashboard.errors(self.principal, run_id="run-shared")
        llm_errors = self.dashboard.errors(
            self.principal, event_type=TraceEventType.llm_called
        )
        html = self.dashboard.render_html(self.principal)

        self.assertEqual(errors["total"], 4)
        self.assertEqual(errors["run_count"], 3)
        self.assertEqual(errors["counts"], {"trace": 4})
        self.assertEqual(
            errors["category_counts"],
            {"audio": 1, "llm": 1, "tool": 1, "workflow": 1},
        )
        grouped = {run["run_id"]: run for run in errors["runs"]}
        self.assertEqual(grouped["run-shared"]["count"], 2)
        self.assertEqual(grouped["run-shared"]["categories"], {"tool": 1, "llm": 1})
        self.assertEqual(audio_errors["total"], 1)
        self.assertEqual(audio_errors["items"][0]["run_id"], "run-audio")
        self.assertEqual(shared_run["total"], 2)
        self.assertEqual(llm_errors["total"], 1)
        self.assertEqual(llm_errors["items"][0]["operation"], "reminder_extraction")
        self.assertIn('data-error-filter="category"', html)
        self.assertIn('data-error-filter="run_id"', html)
        self.assertIn('data-category="audio"', html)
        self.assertIn("unsupported audio format", html)

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
            self.request(
                tenant_b,
                message_id="99",
                text="recuerdame tenant-b secret el martes a las 11 am",
            ),
        )

        snapshot = self.dashboard.snapshot(self.principal)
        payload = json.dumps(snapshot, sort_keys=True)

        self.assertIn("Project Alpha", payload)
        self.assertNotIn("Project Beta", payload)
        self.assertNotIn("tenant-b", payload)

    def test_dashboard_uses_public_adapter_list_methods(self) -> None:
        now = datetime(2026, 6, 23, 16, 31, tzinfo=UTC)
        event = CloudEvent(
            id="evt-public",
            type="reminder.created",
            source="test",
            tenant_id="tenant-a",
            time=now,
        )
        trace = TraceEvent(
            trace_id="trace-public",
            run_id="run-public",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_completed,
            tenant_id="tenant-a",
            timestamp=now,
        )
        state = WorkflowState(
            workflow_id="wf-public",
            tenant_id="tenant-a",
            workflow_type="reminder.create",
            status=WorkflowStatus.completed,
            idempotency_key="wf-public",
            updated_at=now,
        )
        message = OutboxMessage(
            tenant_id="tenant-a", event=event, idempotency_key="out-public"
        )
        reminder = ScheduledReminder(
            reminder_id="rem-public",
            tenant_id="tenant-a",
            calendar_event_id="cal-public",
            notify_at=now,
            channel="telegram",
            recipient="chat-1",
            body="recordatorio",
            idempotency_key="rem-public",
            timezone="America/Bogota",
            source_event_id="event-public",
            payload_fingerprint="e" * 64,
        )
        calendar_event = CalendarEventResult(
            event_id="cal-public",
            title="Public calendar event",
            starts_at=now,
            idempotency_key="cal-public",
            timezone="America/Bogota",
            source_event_id="event-public",
            payload_fingerprint="e" * 64,
        )
        memory = self.container.memory.add(
            self.principal,
            kind=MemoryKind.semantic,
            text="Public adapter memory",
            source="test",
            confirmed=True,
        )
        adapters = {
            "traces": PublicOnlyTenantListAdapter([trace]),
            "outbox": PublicOnlyTenantListAdapter([message]),
            "scheduler": PublicOnlyTenantListAdapter([reminder]),
            "calendar": PublicOnlyCalendarAdapter([calendar_event]),
            "event_store": PublicOnlyTenantListAdapter([event]),
            "states": PublicOnlyTenantListAdapter([state]),
            "memory": PublicOnlyTenantListAdapter([memory]),
        }
        dashboard = AdminDashboard(SimpleNamespace(**adapters))

        snapshot = dashboard.snapshot(self.principal, now=now)

        self.assertEqual(snapshot["events"]["total"], 1)
        self.assertEqual(snapshot["outbox"]["total"], 1)
        self.assertEqual(snapshot["scheduler"]["total"], 1)
        self.assertEqual(snapshot["agenda"]["total"], 1)
        self.assertEqual(snapshot["reminders"]["total"], 1)
        self.assertEqual(snapshot["states"]["total"], 1)
        self.assertEqual(snapshot["memory"]["total"], 1)
        self.assertEqual(snapshot["traces"]["total"], 1)
        for adapter in adapters.values():
            self.assertTrue(adapter.seen_principals)
            self.assertTrue(
                all(principal.is_trusted for principal in adapter.seen_principals)
            )

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
