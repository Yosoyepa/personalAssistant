from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus, LLMResult
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.domain.reminders.parser import extract_reminder
from personal_assistant.domain.reminders.models import (
    ParsedReminder,
    ReminderClarificationReason,
    ReminderUnsupportedReason,
    UnsupportedReminder,
)
from personal_assistant.domain.reminders.workflow_state import ReminderDraft
from personal_assistant.adapters.outbound.notifications.local import (
    LocalNotificationTool,
)
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.exceptions import AssistantError
from personal_assistant.domain.reminders.idempotency import (
    ReminderIdempotencyConflict,
    ReminderPayload,
)
from personal_assistant.application.dto.tracing import TraceEventType
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.in_memory_uow import (
    InMemoryReminderUnitOfWork,
)
from personal_assistant.adapters.observability.local import TraceRecorder


class FakeLLMProvider:
    def complete(self, request, *, budget: TokenBudget) -> LLMResult:
        return LLMResult(
            provider="fake",
            model="fake-model",
            data={
                "is_reminder": True,
                "title": "almorzar con Ana",
                "starts_at": "2026-06-20T15:33:00+00:00",
                "confidence": 0.91,
            },
            input_tokens=20,
            output_tokens=15,
        )


class ReminderWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.now = datetime(2026, 6, 20, 12, tzinfo=UTC)
        self.calendar = LocalCalendarTool()
        self.notifications = LocalNotificationTool()
        self.scheduler = ReminderScheduler()
        self.event_store = InMemoryEventStore()
        self.outbox = InMemoryOutbox()
        self.states = InMemoryWorkflowStateStore()
        self.traces = TraceRecorder()
        self.unit_of_work = InMemoryReminderUnitOfWork(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
        )
        self.workflow = ReminderWorkflow(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            traces=self.traces,
            unit_of_work=self.unit_of_work,
        )

    def key(self, source_event_id: str) -> str:
        return reminder_idempotency_key(
            tenant_id=self.principal.tenant_id,
            channel="telegram",
            principal_id=self.principal.principal_id,
            conversation_id="chat-1",
            source_event_id=source_event_id,
        )

    def request(self, approval: ApprovalGrant | None = None) -> ReminderWorkflowInput:
        text = "recuérdame clase el martes a las 17"
        key = self.key("42")
        if approval is None:
            approval = ApprovalGrant.issue(
                principal=self.principal,
                action="calendar.create_event",
                resource=f"{key}:calendar",
                tier=PermissionTier.P3,
            )
        return ReminderWorkflowInput(
            message_id="42",
            source_event_id="42",
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=datetime(2026, 6, 20, 12, tzinfo=UTC),
            idempotency_key=key,
            approval=approval,
        )

    def request_without_approval(self) -> ReminderWorkflowInput:
        text = "recuérdame clase el martes a las 17"
        return ReminderWorkflowInput(
            message_id="42",
            source_event_id="42",
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=datetime(2026, 6, 20, 12, tzinfo=UTC),
            idempotency_key=self.key("42"),
            approval=None,
        )

    def test_extract_reminder_parses_weekday_and_hour(self) -> None:
        result = extract_reminder(
            "recuérdame clase el martes a las 17",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="UTC",
        )

        self.assertIsInstance(result, ParsedReminder)
        assert isinstance(result, ParsedReminder)
        extraction = result.extraction
        self.assertEqual(extraction.starts_at.weekday(), 1)
        self.assertEqual(extraction.starts_at.hour, 17)

    def test_extract_reminder_parses_natural_time_only_cita(self) -> None:
        result = extract_reminder(
            "agendarme una cita mañana a las 3:33 pm para comer",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="UTC",
        )

        self.assertIsInstance(result, ParsedReminder)
        assert isinstance(result, ParsedReminder)
        extraction = result.extraction
        self.assertEqual(extraction.starts_at.date().isoformat(), "2026-06-21")
        self.assertEqual(extraction.starts_at.hour, 15)
        self.assertEqual(extraction.starts_at.minute, 33)
        self.assertIn("comer", extraction.title)

    def test_extract_reminder_parses_relative_minutes(self) -> None:
        now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
        result = extract_reminder("recuérdame en 2 minutos de pagar el arriendo", now)

        self.assertIsInstance(result, ParsedReminder)
        assert isinstance(result, ParsedReminder)
        extraction = result.extraction
        self.assertEqual(extraction.starts_at, now + timedelta(minutes=2))
        self.assertEqual(extraction.notify_at, now + timedelta(minutes=2))
        self.assertIn("pagar arriendo", extraction.title)

    def test_extract_reminder_parses_spelled_relative_minutes_from_voice_transcript(
        self,
    ) -> None:
        now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
        result = extract_reminder(
            "Necesito que me recuerdes dentro de dos minutos el revisar mis tareas de la universidad.",
            now,
        )

        self.assertIsInstance(result, ParsedReminder)
        assert isinstance(result, ParsedReminder)
        extraction = result.extraction
        self.assertEqual(extraction.starts_at, now + timedelta(minutes=2))
        self.assertEqual(extraction.notify_at, now + timedelta(minutes=2))
        self.assertIn("revisar mis tareas", extraction.title)

    def test_happy_path_creates_calendar_event_and_schedules_notice(self) -> None:
        result = self.workflow.run(self.principal, self.request())

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertIsNotNone(result.calendar_event_id)
        self.assertIsNotNone(result.reminder_id)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)
        due_jobs = self.scheduler.due(
            self.principal, datetime(2026, 6, 23, 21, 31, tzinfo=UTC)
        )
        self.assertEqual(len(due_jobs), 1)
        self.assertEqual(
            due_jobs[0].body, AssistantReplies().reminder_notification_body("clase")
        )
        self.assertEqual(len(self.event_store.list_for_tenant(self.principal)), 1)
        self.assertEqual(len(self.outbox.claim(self.principal)), 1)
        trace_types = [
            event.event_type
            for event in self.traces.list_for_tenant(self.principal.tenant_id)
        ]
        self.assertIn(TraceEventType.agent_started, trace_types)
        self.assertIn(TraceEventType.guardrail_checked, trace_types)
        self.assertIn(TraceEventType.context_selected, trace_types)
        self.assertIn(TraceEventType.tool_called, trace_types)
        self.assertIn(TraceEventType.agent_completed, trace_types)

    def test_workflow_persists_canonical_utc_and_iana_timezone(self) -> None:
        request = self.request()

        result = self.workflow.run(self.principal, request)

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertEqual(result.source_event_id, request.source_event_id)
        self.assertEqual(result.timezone, "America/Bogota")
        self.assertRegex(result.payload_fingerprint, r"^[0-9a-f]{64}$")
        [calendar_event] = self.calendar.list_events(self.principal)
        self.assertEqual(
            calendar_event.starts_at, datetime(2026, 6, 23, 22, tzinfo=UTC)
        )
        self.assertEqual(calendar_event.timezone, result.timezone)
        self.assertEqual(calendar_event.source_event_id, result.source_event_id)
        self.assertEqual(calendar_event.payload_fingerprint, result.payload_fingerprint)
        [scheduled] = self.scheduler.list_for_tenant(self.principal)
        self.assertEqual(scheduled.timezone, result.timezone)
        self.assertEqual(scheduled.source_event_id, result.source_event_id)
        self.assertEqual(scheduled.payload_fingerprint, result.payload_fingerprint)
        self.assertEqual(scheduled.notify_at.tzinfo, UTC)
        [event] = self.event_store.list_for_tenant(self.principal)
        self.assertEqual(event.source_event_id, result.source_event_id)
        self.assertEqual(event.payload_fingerprint, result.payload_fingerprint)
        self.assertEqual(event.timezone, result.timezone)
        self.assertEqual(event.causation_id, result.source_event_id)
        self.assertEqual(event.data["starts_at"], "2026-06-23T22:00:00+00:00")
        self.assertEqual(event.data["timezone"], "America/Bogota")
        state = self.states.get_by_idempotency_key(
            self.principal, request.idempotency_key or ""
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.payload_fingerprint, result.payload_fingerprint)
        self.assertEqual(state.data["source_event_id"], result.source_event_id)
        self.assertEqual(state.data["timezone"], "America/Bogota")

    def test_reminder_draft_round_trip_canonicalizes_local_offset_to_utc(self) -> None:
        draft = ReminderDraft(
            title="clase",
            timezone="America/Bogota",
            starts_at=datetime(2026, 6, 20, 17, tzinfo=ZoneInfo("America/Bogota")),
            notify_at=datetime(2026, 6, 20, 16, 30, tzinfo=ZoneInfo("America/Bogota")),
            confidence=0.9,
        )

        restored = ReminderDraft.from_mapping(draft.to_workflow_data())

        self.assertEqual(restored.starts_at, datetime(2026, 6, 20, 22, tzinfo=UTC))
        self.assertEqual(restored.notify_at, datetime(2026, 6, 20, 21, 30, tzinfo=UTC))
        self.assertEqual(restored.timezone, "America/Bogota")

    def test_workflow_input_canonicalizes_processing_clock_to_utc(self) -> None:
        request = ReminderWorkflowInput(
            message_id="clock-1",
            source_event_id="event-clock-1",
            conversation_id="chat-1",
            text="recuérdame mañana a las 17",
            recipient="chat-1",
            now=datetime(2026, 6, 20, 7, tzinfo=ZoneInfo("America/Bogota")),
            timezone="America/Bogota",
        )

        self.assertEqual(request.now, datetime(2026, 6, 20, 12, tzinfo=UTC))

    def test_clarification_reply_identity_is_versioned_by_typed_reason(self) -> None:
        key = self.key("clarification-1")
        request = ReminderWorkflowInput(
            message_id="message-clarification-1",
            source_event_id="clarification-1",
            conversation_id="chat-1",
            text="recuérdame mañana",
            recipient="chat-1",
            now=self.now,
            idempotency_key=key,
            approval=None,
        )

        first = self.workflow.run(self.principal, request)
        replay = self.workflow.run(self.principal, request)
        state = self.states.get_by_idempotency_key(self.principal, key)

        self.assertEqual(
            first.clarification_reason, ReminderClarificationReason.missing_time
        )
        self.assertEqual(first.clarification_reply_id, "reminder_missing_time")
        self.assertEqual(first.clarification_reply_version, "v1")
        self.assertEqual(replay.clarification_reply_id, first.clarification_reply_id)
        self.assertEqual(replay.clarification_reply_version, "v1")
        self.assertTrue(replay.reused)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.data["clarification_reason"], "missing_time")
        self.assertEqual(state.data["clarification_reply_id"], "reminder_missing_time")
        self.assertEqual(state.data["clarification_reply_version"], "v1")

    def test_invalid_requested_timezone_returns_versioned_clarification_without_effects(
        self,
    ) -> None:
        key = self.key("invalid-timezone-1")
        request = ReminderWorkflowInput(
            message_id="message-invalid-timezone-1",
            source_event_id="invalid-timezone-1",
            conversation_id="chat-1",
            text="recuérdame mañana a las 17",
            recipient="chat-1",
            now=self.now,
            timezone="Mars/Olympus_Mons",
            idempotency_key=key,
            approval=None,
        )

        result = self.workflow.run(self.principal, request)

        self.assertEqual(result.status, AgentStatus.needs_clarification)
        self.assertEqual(
            result.clarification_reason,
            ReminderClarificationReason.invalid_timezone,
        )
        self.assertEqual(result.clarification_reply_id, "reminder_invalid_timezone")
        self.assertEqual(result.clarification_reply_version, "v1")
        self.assertEqual(result.timezone, "Mars/Olympus_Mons")
        self.assertFalse(result.approval_required)
        self.assertEqual(self.calendar.list_events(self.principal), [])
        self.assertEqual(self.scheduler.list_for_tenant(self.principal), [])
        self.assertEqual(self.event_store.list_for_tenant(self.principal), [])
        self.assertEqual(self.outbox.list_for_tenant(self.principal), [])

    def test_reminder_draft_rejects_non_iana_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid IANA timezone"):
            ReminderDraft(
                title="clase",
                timezone="UTC-5",
                starts_at=datetime(2026, 6, 20, 22, tzinfo=UTC),
                confidence=0.9,
            )

    def test_custom_reminder_lead_time_schedules_notice(self) -> None:
        workflow = ReminderWorkflow(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            traces=self.traces,
            unit_of_work=self.unit_of_work,
            reminder_minutes_before=2,
        )

        result = workflow.run(self.principal, self.request())

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertIn("2 minutos antes", result.reply)
        jobs = self.scheduler.due(
            self.principal, datetime(2026, 6, 24, 0, 0, tzinfo=UTC)
        )
        self.assertEqual(len(jobs), 1)
        events = self.calendar.list_events(self.principal)
        self.assertEqual(jobs[0].notify_at, events[0].starts_at - timedelta(minutes=2))
        self.assertEqual(
            len(
                self.scheduler.due(
                    self.principal, jobs[0].notify_at - timedelta(minutes=1)
                )
            ),
            0,
        )
        self.assertEqual(len(self.scheduler.due(self.principal, jobs[0].notify_at)), 1)

    def test_relative_reminder_schedules_notice_at_requested_time(self) -> None:
        text = "recuérdame en 2 minutos pagar el arriendo"
        key = self.key("relative-1")
        workflow = ReminderWorkflow(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            traces=self.traces,
            unit_of_work=self.unit_of_work,
            reminder_minutes_before=2,
        )
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="calendar.create_event",
            resource=f"{key}:calendar",
            tier=PermissionTier.P3,
        )

        result = workflow.run(
            self.principal,
            ReminderWorkflowInput(
                message_id="relative-1",
                source_event_id="relative-1",
                conversation_id="chat-1",
                text=text,
                recipient="chat-1",
                now=self.now,
                idempotency_key=key,
                approval=approval,
            ),
        )

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertIn("momento indicado", result.reply)
        self.assertEqual(
            len(self.scheduler.due(self.principal, self.now + timedelta(minutes=1))), 0
        )
        self.assertEqual(
            len(self.scheduler.due(self.principal, self.now + timedelta(minutes=2))), 1
        )

    def test_duplicate_webhook_reuses_completed_state(self) -> None:
        first = self.workflow.run(self.principal, self.request())
        second = self.workflow.run(self.principal, self.request())

        self.assertEqual(first.calendar_event_id, second.calendar_event_id)
        self.assertEqual(first.reminder_id, second.reminder_id)
        self.assertTrue(second.reused)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)
        self.assertEqual(len(self.event_store.list_for_tenant(self.principal)), 1)

    def test_same_identity_with_changed_payload_conflicts_before_effects(self) -> None:
        first = self.workflow.run(self.principal, self.request())
        changed = self.request().model_copy(
            update={"text": "recuérdame pagar mañana a las 5"}
        )

        with self.assertRaises(ReminderIdempotencyConflict) as captured:
            self.workflow.run(self.principal, changed)

        self.assertEqual(
            captured.exception.response.error.context["idempotency_key"],
            first.idempotency_key,
        )
        self.assertNotIn(changed.text, str(captured.exception.model_dump()))
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)
        self.assertEqual(len(self.scheduler.list_for_tenant(self.principal)), 1)
        self.assertEqual(len(self.event_store.list_for_tenant(self.principal)), 1)
        self.assertEqual(len(self.outbox.list_for_tenant(self.principal)), 1)

    def test_legacy_client_key_cannot_bypass_v2_identity(self) -> None:
        request = self.request().model_copy(
            update={"idempotency_key": "reminder:legacy"}
        )

        with self.assertRaises(ReminderIdempotencyConflict):
            self.workflow.run(self.principal, request)

        self.assertEqual(self.calendar.list_events(self.principal), [])
        self.assertEqual(self.states.list_for_tenant(self.principal), [])

    def test_waiting_approval_replay_with_legitimate_grant_resumes(self) -> None:
        waiting = self.request_without_approval()
        first = self.workflow.run(self.principal, waiting)
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="calendar.create_event",
            resource=f"{first.idempotency_key}:calendar",
            tier=PermissionTier.P3,
        )

        resumed = self.workflow.run(
            self.principal, waiting.model_copy(update={"approval": approval})
        )

        self.assertEqual(first.status, AgentStatus.escalated)
        self.assertEqual(resumed.status, AgentStatus.completed)
        self.assertEqual(resumed.idempotency_key, first.idempotency_key)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)

    def test_invalid_approval_cannot_acquire_waiting_resume(self) -> None:
        waiting = self.request_without_approval()
        first = self.workflow.run(self.principal, waiting)
        invalid = ApprovalGrant(
            approval_id="untrusted",
            tenant_id=self.principal.tenant_id,
            principal_id=self.principal.principal_id,
            action="calendar.create_event",
            resource=f"{first.idempotency_key}:calendar",
            tier=PermissionTier.P3,
        )

        with self.assertRaises(AssistantError):
            self.workflow.run(
                self.principal, waiting.model_copy(update={"approval": invalid})
            )

        state = self.states.get_by_idempotency_key(
            self.principal, first.idempotency_key
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.status, WorkflowStatus.waiting_approval)
        self.assertEqual(self.calendar.list_events(self.principal), [])

    def test_concurrent_legitimate_approvals_execute_effects_once(self) -> None:
        waiting = self.request_without_approval()
        first = self.workflow.run(self.principal, waiting)
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="calendar.create_event",
            resource=f"{first.idempotency_key}:calendar",
            tier=PermissionTier.P3,
        )
        approved = waiting.model_copy(update={"approval": approval})

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _: self.workflow.run(self.principal, approved), range(16)
                )
            )

        self.assertTrue(
            any(result.status == AgentStatus.completed for result in results)
        )
        self.assertTrue(
            all(result.status == AgentStatus.completed for result in results)
        )
        self.assertEqual(sum(not result.reused for result in results), 1)
        self.assertEqual(len(self.calendar.list_events(self.principal)), 1)
        self.assertEqual(len(self.scheduler.list_for_tenant(self.principal)), 1)
        self.assertEqual(len(self.event_store.list_for_tenant(self.principal)), 1)
        self.assertEqual(len(self.outbox.list_for_tenant(self.principal)), 1)

    def test_matching_running_replay_does_not_become_second_executor(self) -> None:
        request = self.request()
        self.states.register_or_replay(
            self.principal,
            WorkflowState(
                tenant_id=self.principal.tenant_id,
                workflow_type="reminder.create",
                status=WorkflowStatus.running,
                step="classify",
                idempotency_key=self.key("42"),
                payload_fingerprint=ReminderPayload(
                    text=request.text,
                    recipient=request.recipient,
                    timezone=request.timezone,
                ).fingerprint,
            ),
        )

        replay = self.workflow.run(self.principal, request)

        self.assertEqual(replay.status, AgentStatus.escalated)
        self.assertTrue(replay.reused)
        self.assertEqual(self.calendar.list_events(self.principal), [])
        self.assertEqual(self.event_store.list_for_tenant(self.principal), [])

    def test_processing_clock_is_not_part_of_replay_payload(self) -> None:
        first = self.workflow.run(self.principal, self.request())
        later = self.request().model_copy(update={"now": self.now + timedelta(days=30)})

        replay = self.workflow.run(self.principal, later)

        self.assertEqual(replay.status, AgentStatus.completed)
        self.assertEqual(replay.calendar_event_id, first.calendar_event_id)
        self.assertTrue(replay.reused)

    def test_explicit_source_event_is_distinct_from_message_reference(self) -> None:
        base = self.request_without_approval().model_copy(
            update={"idempotency_key": None}
        )

        first = self.workflow.run(
            self.principal, base.model_copy(update={"source_event_id": "update-100"})
        )
        second = self.workflow.run(
            self.principal, base.model_copy(update={"source_event_id": "update-101"})
        )

        self.assertNotEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(len(self.states.list_for_tenant(self.principal)), 2)

    def test_missing_approval_does_not_create_side_effect(self) -> None:
        result = self.workflow.run(self.principal, self.request_without_approval())

        self.assertEqual(result.status, AgentStatus.escalated)
        self.assertTrue(result.approval_required)
        self.assertEqual(self.calendar.list_events(self.principal), [])
        self.assertEqual(self.event_store.list_for_tenant(self.principal), [])
        self.assertEqual(self.outbox.claim(self.principal), [])

    def test_llm_fallback_extracts_when_rules_need_help(self) -> None:
        workflow = ReminderWorkflow(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            traces=self.traces,
            unit_of_work=self.unit_of_work,
            llm=FakeLLMProvider(),
        )
        text = "necesito que quede lo de almorzar con Ana a las tres treinta y tres"
        result = workflow.run(
            self.principal,
            ReminderWorkflowInput(
                message_id="llm-1",
                source_event_id="llm-1",
                conversation_id="chat-1",
                text=text,
                recipient="chat-1",
                now=datetime(2026, 6, 20, 12, tzinfo=UTC),
                idempotency_key=self.key("llm-1"),
                approval=None,
            ),
        )

        self.assertEqual(result.status, AgentStatus.escalated)
        self.assertTrue(result.approval_required)
        trace_types = [
            event.event_type
            for event in self.traces.list_for_tenant(self.principal.tenant_id)
        ]
        self.assertIn(TraceEventType.llm_called, trace_types)

    def test_idempotency_key_is_derived_when_missing(self) -> None:
        text = "recuérdame clase el martes a las 17"
        key = self.key("42")
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="calendar.create_event",
            resource=f"{key}:calendar",
            tier=PermissionTier.P3,
        )
        request = ReminderWorkflowInput(
            message_id="42",
            source_event_id="42",
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
        result = extract_reminder(
            "recuérdame clase el martes a las 17",
            datetime(2026, 6, 16, 9, tzinfo=UTC),
            timezone="UTC",
        )

        self.assertIsInstance(result, ParsedReminder)
        assert isinstance(result, ParsedReminder)
        extraction = result.extraction
        self.assertEqual(extraction.starts_at.date().isoformat(), "2026-06-16")
        self.assertEqual(extraction.starts_at.hour, 17)

    def test_pm_and_invalid_minutes_are_handled(self) -> None:
        result = extract_reminder(
            "recuérdame clase el martes a las 9 pm",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
            timezone="UTC",
        )
        invalid = extract_reminder(
            "recuérdame clase el martes a las 5:99",
            datetime(2026, 6, 20, 12, tzinfo=UTC),
        )

        self.assertIsInstance(result, ParsedReminder)
        assert isinstance(result, ParsedReminder)
        extraction = result.extraction
        self.assertEqual(extraction.starts_at.hour, 21)
        self.assertIsInstance(invalid, UnsupportedReminder)
        assert isinstance(invalid, UnsupportedReminder)
        self.assertEqual(invalid.reason, ReminderUnsupportedReason.invalid_time)


if __name__ == "__main__":
    unittest.main()
