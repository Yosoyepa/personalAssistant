from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sys
import unittest

from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
)
from personal_assistant.application.dto.events import (
    CloudEvent,
    OutboxMessage,
    OutboxStatus,
)
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.calendar import (
    CalendarEventRequest,
    CalendarEventResult,
)
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.adapters.persistence import postgres
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.memory.models import MemoryKind, MemoryRecord
from personal_assistant.domain.reminders.idempotency import (
    ReminderIdempotencyConflict,
    ReminderPayload,
)


class RecordingConnection:
    def __init__(
        self,
        *,
        fetchone_results: list[object] | None = None,
        fetchall_results: list[list[object]] | None = None,
    ) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> "RecordingCursor":
        return RecordingCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class RecordingCursor:
    def __init__(self, connection: RecordingConnection) -> None:
        self.connection = connection
        self.closed = False

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def execute(self, statement: str, params: tuple[object, ...] | None = None) -> None:
        self.connection.statements.append((statement, params))

    def fetchone(self) -> object | None:
        if not self.connection.fetchone_results:
            return None
        return self.connection.fetchone_results.pop(0)

    def fetchall(self) -> list[object]:
        if not self.connection.fetchall_results:
            return []
        return self.connection.fetchall_results.pop(0)


class PostgresPersistenceTests(unittest.TestCase):
    def principal(self) -> Principal:
        return Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )

    def cloud_event(
        self, principal: Principal, *, event_id: str = "evt-1"
    ) -> CloudEvent:
        return CloudEvent(
            id=event_id,
            type="test.created",
            source="test",
            tenant_id=principal.tenant_id,
            time=datetime(2026, 6, 28, 12, tzinfo=UTC),
            data={"value": "ok"},
        )

    def pending_approval(
        self, principal: Principal, *, approval_id: str = "apr-1"
    ) -> PendingApproval:
        return PendingApproval(
            approval_id=approval_id,
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            action="calendar.create_event",
            resource="idem-1:calendar",
            tier=PermissionTier.P3.value,
            workflow_kind="reminder.create",
            message_id="msg-1",
            source_event_id="event-1",
            conversation_id="chat-1",
            channel="telegram",
            recipient="chat-1",
            request_text="recuerdame pagar arriendo",
            request_now=datetime(2026, 6, 28, 12, tzinfo=UTC),
            timezone="America/Bogota",
            idempotency_key="idem-1",
            payload_fingerprint="a" * 64,
            created_at=datetime(2026, 6, 28, 12, tzinfo=UTC),
        )

    def test_importing_postgres_adapter_does_not_import_psycopg(self) -> None:
        if "psycopg" in sys.modules:
            self.skipTest("psycopg was already imported by the test process")

        self.assertFalse("psycopg" in sys.modules)
        self.assertTrue(hasattr(postgres, "PostgresEventStore"))
        self.assertFalse("psycopg" in sys.modules)

    def test_ensure_schema_is_idempotent_and_uses_jsonb_tables(self) -> None:
        connection = RecordingConnection()

        postgres.ensure_schema(connection=connection, schema="assistant_test")
        postgres.ensure_schema(connection=connection, schema="assistant_test")

        statements = [statement for statement, _ in connection.statements]
        table_statements = [
            statement
            for statement in statements
            if "CREATE TABLE IF NOT EXISTS" in statement
        ]

        self.assertEqual(connection.commits, 2)
        self.assertEqual(len(table_statements), 16)
        self.assertTrue(all("JSONB" in statement for statement in table_statements))
        self.assertTrue(
            all(
                "IF NOT EXISTS" in statement
                for statement in statements
                if "CREATE " in statement
            )
        )
        self.assertIn('CREATE SCHEMA IF NOT EXISTS "assistant_test"', statements[0])
        self.assertTrue(
            any(
                "ADD COLUMN IF NOT EXISTS payload_fingerprint TEXT" in statement
                for statement in statements
            )
        )

    def test_schema_identifier_is_validated_before_sql_is_built(self) -> None:
        connection = RecordingConnection()

        with self.assertRaises(ValueError):
            postgres.ensure_schema(connection=connection, schema="assistant-test")

        self.assertEqual(connection.statements, [])

    def test_event_store_append_serializes_payload_as_jsonb_parameter(self) -> None:
        principal = self.principal()
        event = CloudEvent(
            id="evt-1",
            type="test.created",
            source="test",
            tenant_id=principal.tenant_id,
            time=datetime(2026, 6, 28, 12, tzinfo=UTC),
            data={"value": "ok"},
        )
        connection = RecordingConnection(
            fetchone_results=[{"payload": event.model_dump(mode="json")}]
        )
        store = postgres.PostgresEventStore(connection=connection)

        saved = store.append(principal, event)

        insert_sql, insert_params = connection.statements[0]
        assert insert_params is not None
        self.assertIn("%s::jsonb", insert_sql)
        self.assertEqual(json.loads(insert_params[-1])["id"], "evt-1")
        self.assertEqual(saved.id, event.id)
        self.assertEqual(saved.tenant_id, principal.tenant_id)

    def test_approval_store_lists_visible_requests_for_principal(self) -> None:
        principal = self.principal()
        approval = self.pending_approval(principal)
        approved = approval.model_copy(
            update={
                "approval_id": "apr-2",
                "idempotency_key": "idem-2",
                "status": PendingApprovalStatus.approved,
            }
        )
        connection = RecordingConnection(
            fetchall_results=[
                [
                    {"payload": approval.model_dump(mode="json")},
                    {"payload": approved.model_dump(mode="json")},
                ]
            ]
        )
        store = postgres.PostgresApprovalStore(connection=connection)

        rows = store.list_for_tenant(principal)

        statement, params = connection.statements[0]
        normalized_statement = " ".join(statement.split())
        self.assertIn(
            "WHERE tenant_id = %s AND principal_id = %s", normalized_statement
        )
        self.assertIn("ORDER BY created_at, approval_id", normalized_statement)
        self.assertEqual(params, (principal.tenant_id, principal.principal_id))
        self.assertEqual([row.approval_id for row in rows], ["apr-1", "apr-2"])
        self.assertEqual(rows[1].status, PendingApprovalStatus.approved)

    def test_approval_store_upgrades_legacy_identity_metadata_on_read(self) -> None:
        principal = self.principal()
        legacy = self.pending_approval(principal).model_dump(mode="json")
        legacy.pop("source_event_id")
        legacy.pop("payload_fingerprint")
        connection = RecordingConnection(fetchone_results=[{"payload": legacy}])
        store = postgres.PostgresApprovalStore(connection=connection)

        restored = store.get(principal, "apr-1")

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.source_event_id, restored.message_id)
        self.assertEqual(
            restored.payload_fingerprint,
            ReminderPayload(
                text=restored.request_text,
                recipient=restored.recipient,
                timezone=restored.timezone,
            ).fingerprint,
        )

    def test_approval_store_replays_equivalent_legacy_row_with_old_hash(self) -> None:
        principal = self.principal()
        approval = self.pending_approval(principal)
        canonical_fingerprint = ReminderPayload(
            text=approval.request_text,
            recipient=approval.recipient,
            timezone=approval.timezone,
        ).fingerprint
        approval = approval.model_copy(
            update={
                "source_event_id": approval.message_id,
                "payload_fingerprint": canonical_fingerprint,
            }
        )
        legacy = approval.model_dump(mode="json")
        legacy.pop("source_event_id")
        legacy.pop("payload_fingerprint")
        connection = RecordingConnection(
            fetchone_results=[
                None,
                {"payload": legacy, "fingerprint": "pre-p1-a4-hash"},
            ]
        )
        store = postgres.PostgresApprovalStore(connection=connection)

        restored = store.create(principal, approval)

        self.assertEqual(restored.source_event_id, approval.message_id)
        self.assertEqual(restored.payload_fingerprint, canonical_fingerprint)

    def test_outbox_claim_updates_payload_and_attempts(self) -> None:
        principal = self.principal()
        message = OutboxMessage(
            tenant_id=principal.tenant_id,
            event=self.cloud_event(principal),
            idempotency_key="idem-outbox-1",
            attempts=2,
        )
        connection = RecordingConnection(
            fetchall_results=[[("idem-outbox-1", message.model_dump(mode="json"))]]
        )
        store = postgres.PostgresOutbox(connection=connection)

        claimed = store.claim(principal, limit=1, owner="worker-1", lease_seconds=30)

        select_sql, select_params = connection.statements[0]
        update_sql, update_params = connection.statements[1]
        assert select_params is not None
        assert update_params is not None
        self.assertIn("FOR UPDATE SKIP LOCKED", select_sql)
        self.assertEqual(select_params[0], principal.tenant_id)
        self.assertEqual(select_params[1], OutboxStatus.published.value)
        self.assertEqual(select_params[-1], 1)
        self.assertIn("payload = %s::jsonb", update_sql)
        self.assertEqual(update_params[0], OutboxStatus.claimed.value)
        self.assertTrue(str(update_params[1]).startswith("claim_"))
        self.assertEqual(update_params[2], "worker-1")
        self.assertEqual(update_params[5], 3)
        self.assertEqual(update_params[-2:], (principal.tenant_id, "idem-outbox-1"))
        updated_payload = json.loads(update_params[7])
        self.assertEqual(updated_payload["dispatch_status"], OutboxStatus.claimed.value)
        self.assertEqual(updated_payload["claim_owner"], "worker-1")
        self.assertEqual(updated_payload["attempts"], 3)
        self.assertEqual(claimed[0].dispatch_status, OutboxStatus.claimed)
        self.assertEqual(claimed[0].attempts, 3)

    def test_calendar_create_event_writes_request_and_result_payloads(self) -> None:
        principal = self.principal()
        request = CalendarEventRequest(
            title="pagar arriendo",
            starts_at=datetime(2026, 6, 28, 22, tzinfo=UTC),
            timezone="America/Bogota",
            idempotency_key="idem-calendar-1",
            source_event_id="event-calendar-1",
            payload_fingerprint="b" * 64,
        )
        result = CalendarEventResult(
            event_id="cal-1",
            title=request.title,
            starts_at=request.starts_at,
            idempotency_key=request.idempotency_key,
            timezone=request.timezone,
            source_event_id=request.source_event_id,
            payload_fingerprint=request.payload_fingerprint,
        )
        approval = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource=request.idempotency_key,
            tier=PermissionTier.P3,
            approval_id="apr-1",
        )
        connection = RecordingConnection(
            fetchone_results=[{"payload": result.model_dump(mode="json")}]
        )
        store = postgres.PostgresCalendarStore(connection=connection)

        saved = store.create_event(principal, request, approval=approval)

        insert_sql, insert_params = connection.statements[0]
        assert insert_params is not None
        self.assertIn("%s::jsonb", insert_sql)
        self.assertEqual(insert_params[0], principal.tenant_id)
        self.assertEqual(insert_params[1], request.idempotency_key)
        self.assertEqual(insert_params[3], request.title)
        self.assertEqual(json.loads(insert_params[6])["title"], request.title)
        self.assertEqual(json.loads(insert_params[7])["title"], request.title)
        self.assertTrue(json.loads(insert_params[7])["event_id"].startswith("cal_"))
        self.assertEqual(saved.event_id, "cal-1")

    def test_calendar_store_restores_legacy_result_timezone_from_request(self) -> None:
        principal = self.principal()
        request = CalendarEventRequest(
            title="cita",
            starts_at=datetime(2026, 10, 25, 9, tzinfo=UTC),
            timezone="Europe/Madrid",
            idempotency_key="calendar-legacy",
            source_event_id="event-calendar-legacy",
            payload_fingerprint="d" * 64,
        )
        result = CalendarEventResult(
            event_id="cal-legacy",
            title=request.title,
            starts_at=request.starts_at,
            timezone=request.timezone,
            idempotency_key=request.idempotency_key,
        ).model_dump(mode="json")
        result.pop("timezone")
        connection = RecordingConnection(
            fetchall_results=[
                [
                    {
                        "payload": result,
                        "request_payload": request.model_dump(mode="json"),
                    }
                ]
            ]
        )
        store = postgres.PostgresCalendarStore(connection=connection)

        [restored] = store.list_events(principal)

        self.assertEqual(restored.timezone, "Europe/Madrid")
        self.assertEqual(restored.source_event_id, request.source_event_id)
        self.assertEqual(restored.payload_fingerprint, request.payload_fingerprint)

    def test_scheduler_mark_sent_updates_payload(self) -> None:
        principal = self.principal()
        reminder = ScheduledReminder(
            reminder_id="rem-1",
            tenant_id=principal.tenant_id,
            calendar_event_id="cal-1",
            notify_at=datetime(2026, 6, 28, 21, 58, tzinfo=UTC),
            channel="telegram",
            recipient="chat-1",
            body="Recordatorio: pagar arriendo",
            idempotency_key="idem-reminder-1",
            timezone="America/Bogota",
            source_event_id="event-reminder-1",
            payload_fingerprint="c" * 64,
        )
        connection = RecordingConnection(
            fetchone_results=[("idem-reminder-1", reminder.model_dump(mode="json"))]
        )
        store = postgres.PostgresReminderScheduler(connection=connection)

        saved = store.mark_sent(principal, "rem-1")

        select_sql, select_params = connection.statements[0]
        update_sql, update_params = connection.statements[1]
        assert update_params is not None
        self.assertIn("FOR UPDATE", select_sql)
        self.assertEqual(select_params, (principal.tenant_id, "rem-1"))
        self.assertIn("SET sent = true", update_sql)
        payload = json.loads(update_params[0])
        self.assertTrue(payload["sent"])
        self.assertEqual(update_params[1:], (principal.tenant_id, "idem-reminder-1"))
        self.assertTrue(saved.sent)

    def test_scheduler_store_upgrades_legacy_metadata_deterministically(self) -> None:
        principal = self.principal()
        legacy = ScheduledReminder(
            reminder_id="rem-legacy",
            tenant_id=principal.tenant_id,
            calendar_event_id="cal-legacy",
            notify_at=datetime(2026, 6, 28, 21, 58, tzinfo=UTC),
            timezone="UTC",
            source_event_id="placeholder",
            payload_fingerprint="e" * 64,
            channel="telegram",
            recipient="chat-1",
            body="Recordatorio legacy",
            idempotency_key="idem-reminder-legacy",
        ).model_dump(mode="json")
        legacy.pop("timezone")
        legacy.pop("source_event_id")
        legacy.pop("payload_fingerprint")
        connection = RecordingConnection(
            fetchall_results=[[{"payload": legacy}, {"payload": dict(legacy)}]]
        )
        store = postgres.PostgresReminderScheduler(connection=connection)

        first, second = store.list_for_tenant(principal)

        self.assertEqual(first.timezone, "UTC")
        self.assertEqual(first.source_event_id, "legacy:idem-reminder-legacy")
        self.assertRegex(first.payload_fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(second.payload_fingerprint, first.payload_fingerprint)

    def test_memory_retrieve_scopes_by_tenant_user_kind_confirmed_and_query(
        self,
    ) -> None:
        principal = self.principal()
        record = MemoryRecord(
            tenant_id=principal.tenant_id,
            user_id=principal.actor_id,
            kind=MemoryKind.semantic,
            text="prefiere recordatorios cortos",
            source="test",
            confirmed=True,
        )
        connection = RecordingConnection(
            fetchall_results=[[{"payload": record.model_dump(mode="json")}]]
        )
        store = postgres.PostgresMemoryStore(connection=connection)

        rows = store.retrieve(
            principal,
            query="Recordatorios",
            kind=MemoryKind.semantic,
            confirmed_only=True,
            limit=3,
        )

        statement, params = connection.statements[0]
        assert params is not None
        self.assertIn("WHERE tenant_id = %s", statement)
        self.assertEqual(
            params,
            (
                principal.tenant_id,
                principal.actor_id,
                MemoryKind.semantic.value,
                MemoryKind.semantic.value,
                True,
                "recordatorios",
                "recordatorios",
                3,
            ),
        )
        self.assertEqual(rows[0].id, record.id)

    def test_trace_recorder_write_and_list_for_run(self) -> None:
        principal = self.principal()
        trace = TraceEvent(
            trace_id="trace-1",
            run_id="run-1",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id=principal.tenant_id,
            error={"type": "RuntimeError", "message": "boom"},
        )
        write_connection = RecordingConnection(
            fetchone_results=[{"payload": trace.model_dump(mode="json")}]
        )
        write_store = postgres.PostgresTraceRecorder(connection=write_connection)

        write_store.write(trace)

        insert_sql, insert_params = write_connection.statements[0]
        assert insert_params is not None
        self.assertIn("%s::jsonb", insert_sql)
        self.assertEqual(
            insert_params[:5],
            (
                trace.tenant_id,
                trace.trace_id,
                trace.run_id,
                trace.agent_id,
                trace.event_type.value,
            ),
        )
        self.assertEqual(json.loads(insert_params[-1])["trace_id"], "trace-1")

        list_connection = RecordingConnection(
            fetchall_results=[[{"payload": trace.model_dump(mode="json")}]]
        )
        list_store = postgres.PostgresTraceRecorder(connection=list_connection)

        rows = list_store.list_for_run(principal, "run-1")

        select_sql, select_params = list_connection.statements[0]
        self.assertIn(
            "WHERE tenant_id = %s AND run_id = %s", " ".join(select_sql.split())
        )
        self.assertEqual(select_params, (principal.tenant_id, "run-1"))
        self.assertEqual(rows[0].trace_id, "trace-1")

    def test_trace_recorder_redacts_mutated_payload_before_json_insert(self) -> None:
        trace = TraceEvent(
            trace_id="trace-private",
            run_id="run-private",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id="tenant-a",
        )
        trace.input_summary["transcript"] = "test-only transcript fixture"
        trace.output_summary["audio"] = b"test-only-audio-bytes"
        trace.error["ApiToken"] = "test_placeholder_credential"
        connection = RecordingConnection(fetchone_results=[{"payload": {}}])

        postgres.PostgresTraceRecorder(connection=connection).write(trace)

        _, insert_params = connection.statements[0]
        assert insert_params is not None
        serialized = str(insert_params[-1])
        self.assertNotIn("test-only transcript fixture", serialized)
        self.assertNotIn("test-only-audio-bytes", serialized)
        self.assertNotIn("test_placeholder_credential", serialized)
        payload = json.loads(serialized)
        self.assertEqual(payload["input_summary"]["transcript"], "[REDACTED]")
        self.assertEqual(payload["output_summary"]["audio"]["kind"], "binary")
        self.assertEqual(payload["output_summary"]["audio"]["size_bytes"], 21)
        self.assertEqual(payload["error"]["ApiToken"], "[REDACTED]")

    def test_trace_recorder_redacts_legacy_postgres_payload_on_read(self) -> None:
        principal = self.principal()
        legacy_payload = TraceEvent(
            trace_id="trace-legacy",
            run_id="run-legacy",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id=principal.tenant_id,
        ).model_dump(mode="json")
        legacy_payload["input_summary"] = {
            "input": "test-only private validation input",
            "metadata": {
                "items": [
                    {"AccessToken": "test_placeholder_credential"},
                ]
            },
        }
        legacy_payload["output_summary"] = {"transcript": "test-only legacy transcript"}
        legacy_payload["error"] = {
            "type": "LegacyError",
            "message": "test-only legacy error text",
        }
        connection = RecordingConnection(
            fetchall_results=[[{"payload": legacy_payload}]]
        )

        [restored] = postgres.PostgresTraceRecorder(connection=connection).list_for_run(
            principal, "run-legacy"
        )

        serialized = restored.model_dump_json()
        self.assertNotIn("test-only private validation input", serialized)
        self.assertNotIn("test_placeholder_credential", serialized)
        self.assertNotIn("test-only legacy transcript", serialized)
        self.assertNotIn("test-only legacy error text", serialized)
        self.assertEqual(restored.input_summary["input"], "[REDACTED]")
        self.assertEqual(restored.output_summary["transcript"], "[REDACTED]")
        self.assertEqual(restored.error["message"], "[REDACTED]")

    def test_terminal_workflow_state_conflict_is_preserved(self) -> None:
        principal = self.principal()
        completed = WorkflowState(
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.completed,
            step="completed",
            idempotency_key="same",
            data={"result": "done"},
        )
        regressed = completed.model_copy(
            update={"status": WorkflowStatus.running, "step": "retry"}
        )
        connection = RecordingConnection(
            fetchone_results=[
                (
                    completed.model_dump(mode="json"),
                    "different-fingerprint",
                    WorkflowStatus.completed.value,
                    None,
                )
            ]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        with self.assertRaises(AssistantError) as ctx:
            store.upsert(principal, regressed)

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(len(connection.statements), 1)
        self.assertEqual(connection.rollbacks, 1)

    def test_workflow_registration_uses_atomic_insert_and_separate_payload_fingerprint(
        self,
    ) -> None:
        principal = self.principal()
        state = WorkflowState(
            workflow_id="wf-register",
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.running,
            step="classify",
            idempotency_key=f"reminder:v2:{'a' * 64}",
            payload_fingerprint="b" * 64,
        )
        connection = RecordingConnection(
            fetchone_results=[{"payload": state.model_dump(mode="json")}]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        registration = store.register_or_replay(principal, state)

        statement, params = connection.statements[0]
        assert params is not None
        self.assertIn("ON CONFLICT (tenant_id, idempotency_key) DO NOTHING", statement)
        self.assertIn("payload_fingerprint", statement)
        self.assertEqual(params[0], principal.tenant_id)
        self.assertEqual(params[1], state.idempotency_key)
        self.assertEqual(params[8], state.payload_fingerprint)
        self.assertFalse(registration.replayed)
        self.assertEqual(registration.state, state)

    def test_workflow_registration_replays_matching_payload_after_conflict(
        self,
    ) -> None:
        principal = self.principal()
        state = WorkflowState(
            workflow_id="wf-existing",
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.waiting_approval,
            step="approval_required",
            idempotency_key=f"reminder:v2:{'a' * 64}",
            payload_fingerprint="b" * 64,
        )
        duplicate = state.model_copy(update={"workflow_id": "wf-candidate"})
        connection = RecordingConnection(
            fetchone_results=[
                None,
                (state.model_dump(mode="json"), state.payload_fingerprint),
            ]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        registration = store.register_or_replay(principal, duplicate)

        self.assertTrue(registration.replayed)
        self.assertEqual(registration.state.workflow_id, "wf-existing")
        self.assertEqual(len(connection.statements), 2)
        self.assertIn("FOR UPDATE", connection.statements[1][0])
        self.assertEqual(
            connection.statements[1][1], (principal.tenant_id, state.idempotency_key)
        )

    def test_workflow_registration_atomically_resumes_matching_waiting_step(
        self,
    ) -> None:
        principal = self.principal()
        waiting = WorkflowState(
            workflow_id="wf-existing",
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.waiting_approval,
            step="approval_required",
            idempotency_key=f"reminder:v2:{'a' * 64}",
            payload_fingerprint="b" * 64,
            data={"title": "pagar"},
        )
        resumed = waiting.model_copy(update={"status": WorkflowStatus.running})
        connection = RecordingConnection(
            fetchone_results=[
                None,
                (waiting.model_dump(mode="json"), waiting.payload_fingerprint),
                {"payload": resumed.model_dump(mode="json")},
            ]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        registration = store.register_or_replay(
            principal,
            waiting.model_copy(
                update={"workflow_id": "wf-candidate", "status": WorkflowStatus.running}
            ),
            resume_from_step="approval_required",
        )

        self.assertTrue(registration.resumed)
        self.assertFalse(registration.replayed)
        self.assertEqual(registration.state.workflow_id, waiting.workflow_id)
        self.assertEqual(registration.state.status, WorkflowStatus.running)
        self.assertEqual(len(connection.statements), 3)
        resume_sql, resume_params = connection.statements[2]
        assert resume_params is not None
        self.assertTrue(resume_sql.lstrip().startswith("UPDATE"))
        self.assertIn("AND status = %s", resume_sql)
        self.assertIn("AND step = %s", resume_sql)
        self.assertEqual(
            resume_params[-3:],
            (WorkflowStatus.waiting_approval.value, "approval_required", "b" * 64),
        )

    def test_workflow_registration_rejects_changed_payload_without_update(self) -> None:
        principal = self.principal()
        persisted = WorkflowState(
            workflow_id="wf-existing",
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.running,
            step="classify",
            idempotency_key=f"reminder:v2:{'a' * 64}",
            payload_fingerprint="b" * 64,
        )
        candidate = persisted.model_copy(update={"payload_fingerprint": "c" * 64})
        connection = RecordingConnection(
            fetchone_results=[
                None,
                (persisted.model_dump(mode="json"), persisted.payload_fingerprint),
            ]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        with self.assertRaises(ReminderIdempotencyConflict) as captured:
            store.register_or_replay(principal, candidate)

        self.assertEqual(
            captured.exception.response.error.context["idempotency_key"],
            persisted.idempotency_key,
        )
        self.assertEqual(len(connection.statements), 2)
        self.assertTrue(connection.statements[1][0].lstrip().startswith("SELECT"))

    def test_workflow_upsert_cannot_change_or_remove_payload_fingerprint(self) -> None:
        principal = self.principal()
        persisted = WorkflowState(
            workflow_id="wf-existing",
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.running,
            step="classify",
            idempotency_key=f"reminder:v2:{'a' * 64}",
            payload_fingerprint="b" * 64,
        )
        connection = RecordingConnection(
            fetchone_results=[
                (
                    persisted.model_dump(mode="json"),
                    "state-fingerprint",
                    WorkflowStatus.running.value,
                    persisted.payload_fingerprint,
                )
            ]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        with self.assertRaises(ReminderIdempotencyConflict):
            store.upsert(
                principal, persisted.model_copy(update={"payload_fingerprint": None})
            )

        self.assertEqual(len(connection.statements), 1)
        self.assertTrue(connection.statements[0][0].lstrip().startswith("SELECT"))

    def test_workflow_upsert_sql_preserves_registered_identity_columns(self) -> None:
        principal = self.principal()
        persisted = WorkflowState(
            workflow_id="wf-existing",
            tenant_id=principal.tenant_id,
            workflow_type="reminder.create",
            status=WorkflowStatus.running,
            step="classify",
            idempotency_key=f"reminder:v2:{'a' * 64}",
            payload_fingerprint="b" * 64,
        )
        updated = persisted.model_copy(
            update={
                "status": WorkflowStatus.waiting_approval,
                "step": "approval_required",
            }
        )
        connection = RecordingConnection(
            fetchone_results=[
                (
                    persisted.model_dump(mode="json"),
                    "state-fingerprint",
                    WorkflowStatus.running.value,
                    persisted.payload_fingerprint,
                ),
                {"payload": updated.model_dump(mode="json")},
            ]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        saved = store.upsert(principal, updated)

        update_statement = connection.statements[1][0]
        update_clause = update_statement.split("DO UPDATE", 1)[1].split("WHERE", 1)[0]
        self.assertNotIn("workflow_id =", update_clause)
        self.assertNotIn("workflow_type =", update_clause)
        self.assertNotIn("payload_fingerprint =", update_clause)
        self.assertEqual(saved.status, WorkflowStatus.waiting_approval)

    def test_legacy_calendar_without_any_timezone_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing timezone"):
            postgres._upgrade_legacy_calendar_result(
                {
                    "event_id": "cal-legacy",
                    "title": "Legacy",
                    "starts_at": datetime(2026, 7, 17, 15, tzinfo=UTC),
                    "idempotency_key": "calendar-legacy",
                },
                {},
            )

        with self.assertRaisesRegex(ValueError, "provide only one"):
            postgres._PostgresDatabase(
                dsn="postgresql://example.invalid/db",
                connection=RecordingConnection(),
            )

    def test_event_and_outbox_postgres_paths_preserve_tenant_and_conflicts(
        self,
    ) -> None:
        principal = self.principal()
        foreign_event = self.cloud_event(
            Principal.for_test(
                principal_id="user-2",
                tenant_id="tenant-b",
                permission_tier=PermissionTier.P5,
            )
        )
        with self.assertRaises(AssistantError) as event_tenant:
            postgres.PostgresEventStore(connection=RecordingConnection()).append(
                principal, foreign_event
            )
        self.assertEqual(event_tenant.exception.code, ErrorCode.PERMISSION_DENIED)

        with self.assertRaises(AssistantError) as outbox_tenant:
            postgres.PostgresOutbox(connection=RecordingConnection()).add(
                principal,
                foreign_event,
                idempotency_key="outbox-foreign",
            )
        self.assertEqual(outbox_tenant.exception.code, ErrorCode.PERMISSION_DENIED)

        event = self.cloud_event(principal)
        list_connection = RecordingConnection(
            fetchall_results=[[{"payload": event.model_dump(mode="json")}]]
        )
        listed = postgres.PostgresEventStore(
            connection=list_connection
        ).list_for_tenant(principal)
        self.assertEqual(listed, [event])

        for existing, expected_code in (
            (None, ErrorCode.INTERNAL_ERROR),
            (
                {
                    "payload": event.model_dump(mode="json"),
                    "fingerprint": "different-fingerprint",
                },
                ErrorCode.CONFLICT,
            ),
        ):
            connection = RecordingConnection(fetchone_results=[None, existing])
            with self.assertRaises(AssistantError) as captured:
                postgres.PostgresEventStore(connection=connection).append(
                    principal, event
                )
            self.assertEqual(captured.exception.code, expected_code)

        returned_message = OutboxMessage(
            tenant_id=principal.tenant_id,
            event=event,
            idempotency_key="outbox-key",
        )
        created = postgres.PostgresOutbox(
            connection=RecordingConnection(
                fetchone_results=[{"payload": returned_message.model_dump(mode="json")}]
            )
        ).add(principal, event, idempotency_key="outbox-key")
        self.assertEqual(created.idempotency_key, "outbox-key")

        for existing, expected_code in (
            (None, ErrorCode.INTERNAL_ERROR),
            (
                {
                    "payload": returned_message.model_dump(mode="json"),
                    "fingerprint": "different-fingerprint",
                },
                ErrorCode.CONFLICT,
            ),
        ):
            connection = RecordingConnection(fetchone_results=[None, existing])
            with self.assertRaises(AssistantError) as captured:
                postgres.PostgresOutbox(connection=connection).add(
                    principal, event, idempotency_key="outbox-key"
                )
            self.assertEqual(captured.exception.code, expected_code)

    def test_calendar_postgres_replay_handles_legacy_conflict_and_missing_row(
        self,
    ) -> None:
        principal = self.principal()
        request = CalendarEventRequest(
            title="Cita legacy",
            starts_at=datetime(2026, 7, 17, 15, tzinfo=UTC),
            timezone="America/Bogota",
            idempotency_key="calendar-replay",
            source_event_id="event-calendar-replay",
            payload_fingerprint="f" * 64,
        )
        approval = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource=request.idempotency_key,
            tier=PermissionTier.P3,
            approval_id="apr-calendar-replay",
        )
        legacy_result = CalendarEventResult(
            event_id="cal-legacy",
            title=request.title,
            starts_at=request.starts_at,
            timezone=request.timezone,
            idempotency_key=request.idempotency_key,
        ).model_dump(mode="json")
        legacy_result.pop("timezone")
        request_payload = request.model_dump(mode="json")
        replay_row = {
            "payload": legacy_result,
            "request_fingerprint": postgres._fingerprint(request_payload),
            "request_payload": request_payload,
        }

        replayed = postgres.PostgresCalendarStore(
            connection=RecordingConnection(fetchone_results=[None, replay_row])
        ).create_event(principal, request, approval=approval)
        self.assertTrue(replayed.reused)
        self.assertEqual(replayed.timezone, request.timezone)
        self.assertEqual(replayed.source_event_id, request.source_event_id)

        conflict_row = {**replay_row, "request_fingerprint": "different"}
        with self.assertRaises(AssistantError) as conflict:
            postgres.PostgresCalendarStore(
                connection=RecordingConnection(fetchone_results=[None, conflict_row])
            ).create_event(principal, request, approval=approval)
        self.assertEqual(conflict.exception.code, ErrorCode.CONFLICT)

        with self.assertRaises(AssistantError) as missing:
            postgres.PostgresCalendarStore(
                connection=RecordingConnection(fetchone_results=[None, None])
            ).create_event(principal, request, approval=approval)
        self.assertEqual(missing.exception.code, ErrorCode.INTERNAL_ERROR)

    def test_scheduler_postgres_replay_upgrades_legacy_and_detects_conflict(
        self,
    ) -> None:
        principal = self.principal()
        starts_at = datetime(2026, 7, 17, 15, tzinfo=UTC)
        arguments = {
            "calendar_event_id": "cal-1",
            "starts_at": starts_at,
            "channel": "telegram",
            "recipient": "chat-1",
            "body": "Recordatorio",
            "timezone": "America/Bogota",
            "source_event_id": "event-1",
            "payload_fingerprint": "a" * 64,
            "minutes_before": 30,
            "idempotency_key": "scheduler-replay",
        }
        stored = ScheduledReminder(
            reminder_id="rem-stored",
            tenant_id=principal.tenant_id,
            calendar_event_id="cal-1",
            notify_at=starts_at - timedelta(minutes=30),
            channel="telegram",
            recipient="chat-1",
            body="Recordatorio",
            timezone="America/Bogota",
            source_event_id="event-1",
            payload_fingerprint="a" * 64,
            idempotency_key="scheduler-replay",
        )
        legacy = stored.model_dump(mode="json")
        legacy.pop("timezone")
        legacy.pop("source_event_id")
        legacy.pop("payload_fingerprint")

        inserted_legacy = postgres.PostgresReminderScheduler(
            connection=RecordingConnection(fetchone_results=[{"payload": legacy}])
        ).schedule_before_event(principal, **arguments)
        self.assertEqual(inserted_legacy.timezone, "UTC")
        self.assertEqual(inserted_legacy.source_event_id, "legacy:scheduler-replay")

        replayed = postgres.PostgresReminderScheduler(
            connection=RecordingConnection(
                fetchone_results=[None, {"payload": stored.model_dump(mode="json")}]
            )
        ).schedule_before_event(principal, **arguments)
        self.assertEqual(replayed, stored)

        conflicting = stored.model_copy(update={"body": "Contenido diferente"})
        with self.assertRaises(AssistantError) as conflict:
            postgres.PostgresReminderScheduler(
                connection=RecordingConnection(
                    fetchone_results=[
                        None,
                        {"payload": conflicting.model_dump(mode="json")},
                    ]
                )
            ).schedule_before_event(principal, **arguments)
        self.assertEqual(conflict.exception.code, ErrorCode.CONFLICT)

        with self.assertRaises(AssistantError) as missing:
            postgres.PostgresReminderScheduler(
                connection=RecordingConnection(fetchone_results=[None, None])
            ).schedule_before_event(principal, **arguments)
        self.assertEqual(missing.exception.code, ErrorCode.INTERNAL_ERROR)

        due = postgres.PostgresReminderScheduler(
            connection=RecordingConnection(fetchall_results=[[{"payload": legacy}]])
        ).due(principal, datetime(2026, 7, 17, 16, tzinfo=UTC))
        self.assertEqual(due[0].source_event_id, "legacy:scheduler-replay")


if __name__ == "__main__":
    unittest.main()
