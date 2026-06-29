from __future__ import annotations

from datetime import UTC, datetime
import json
import sys
import unittest

from personal_assistant.application.dto.commands import PendingApproval, PendingApprovalStatus
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage, OutboxStatus
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.calendar import CalendarEventRequest, CalendarEventResult
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.adapters.persistence import postgres
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.memory.models import MemoryKind, MemoryRecord


class RecordingConnection:
    def __init__(self, *, fetchone_results: list[object] | None = None, fetchall_results: list[list[object]] | None = None) -> None:
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

    def cloud_event(self, principal: Principal, *, event_id: str = "evt-1") -> CloudEvent:
        return CloudEvent(
            id=event_id,
            type="test.created",
            source="test",
            tenant_id=principal.tenant_id,
            time=datetime(2026, 6, 28, 12, tzinfo=UTC),
            data={"value": "ok"},
        )

    def pending_approval(self, principal: Principal, *, approval_id: str = "apr-1") -> PendingApproval:
        return PendingApproval(
            approval_id=approval_id,
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            action="calendar.create_event",
            resource="idem-1:calendar",
            tier=PermissionTier.P3.value,
            workflow_kind="reminder.create",
            message_id="msg-1",
            conversation_id="chat-1",
            channel="telegram",
            recipient="chat-1",
            request_text="recuerdame pagar arriendo",
            request_now=datetime(2026, 6, 28, 12, tzinfo=UTC),
            timezone="America/Bogota",
            idempotency_key="idem-1",
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
        table_statements = [statement for statement in statements if "CREATE TABLE IF NOT EXISTS" in statement]

        self.assertEqual(connection.commits, 2)
        self.assertEqual(len(table_statements), 16)
        self.assertTrue(all("JSONB" in statement for statement in table_statements))
        self.assertTrue(all("IF NOT EXISTS" in statement for statement in statements if "CREATE " in statement))
        self.assertIn('CREATE SCHEMA IF NOT EXISTS "assistant_test"', statements[0])

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
        connection = RecordingConnection(fetchone_results=[{"payload": event.model_dump(mode="json")}])
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
        self.assertIn("WHERE tenant_id = %s AND principal_id = %s", normalized_statement)
        self.assertIn("ORDER BY created_at, approval_id", normalized_statement)
        self.assertEqual(params, (principal.tenant_id, principal.principal_id))
        self.assertEqual([row.approval_id for row in rows], ["apr-1", "apr-2"])
        self.assertEqual(rows[1].status, PendingApprovalStatus.approved)

    def test_outbox_claim_updates_payload_and_attempts(self) -> None:
        principal = self.principal()
        message = OutboxMessage(
            tenant_id=principal.tenant_id,
            event=self.cloud_event(principal),
            idempotency_key="idem-outbox-1",
            attempts=2,
        )
        connection = RecordingConnection(fetchall_results=[[("idem-outbox-1", message.model_dump(mode="json"))]])
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
        )
        result = CalendarEventResult(
            event_id="cal-1",
            title=request.title,
            starts_at=request.starts_at,
            idempotency_key=request.idempotency_key,
        )
        approval = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource=request.idempotency_key,
            tier=PermissionTier.P3,
            approval_id="apr-1",
        )
        connection = RecordingConnection(fetchone_results=[{"payload": result.model_dump(mode="json")}])
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
        )
        connection = RecordingConnection(fetchone_results=[("idem-reminder-1", reminder.model_dump(mode="json"))])
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

    def test_memory_retrieve_scopes_by_tenant_user_kind_confirmed_and_query(self) -> None:
        principal = self.principal()
        record = MemoryRecord(
            tenant_id=principal.tenant_id,
            user_id=principal.actor_id,
            kind=MemoryKind.semantic,
            text="prefiere recordatorios cortos",
            source="test",
            confirmed=True,
        )
        connection = RecordingConnection(fetchall_results=[[{"payload": record.model_dump(mode="json")}]])
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
        write_connection = RecordingConnection(fetchone_results=[{"payload": trace.model_dump(mode="json")}])
        write_store = postgres.PostgresTraceRecorder(connection=write_connection)

        write_store.write(trace)

        insert_sql, insert_params = write_connection.statements[0]
        assert insert_params is not None
        self.assertIn("%s::jsonb", insert_sql)
        self.assertEqual(insert_params[:5], (trace.tenant_id, trace.trace_id, trace.run_id, trace.agent_id, trace.event_type.value))
        self.assertEqual(json.loads(insert_params[-1])["trace_id"], "trace-1")

        list_connection = RecordingConnection(fetchall_results=[[{"payload": trace.model_dump(mode="json")}]])
        list_store = postgres.PostgresTraceRecorder(connection=list_connection)

        rows = list_store.list_for_run(principal, "run-1")

        select_sql, select_params = list_connection.statements[0]
        self.assertIn("WHERE tenant_id = %s AND run_id = %s", " ".join(select_sql.split()))
        self.assertEqual(select_params, (principal.tenant_id, "run-1"))
        self.assertEqual(rows[0].trace_id, "trace-1")

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
        regressed = completed.model_copy(update={"status": WorkflowStatus.running, "step": "retry"})
        connection = RecordingConnection(
            fetchone_results=[(completed.model_dump(mode="json"), "different-fingerprint", WorkflowStatus.completed.value)]
        )
        store = postgres.PostgresWorkflowStateStore(connection=connection)

        with self.assertRaises(AssistantError) as ctx:
            store.upsert(principal, regressed)

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(len(connection.statements), 1)
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
