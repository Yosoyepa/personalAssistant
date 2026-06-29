from __future__ import annotations

from datetime import UTC, datetime
import json
import sys
import unittest

from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.adapters.persistence import postgres
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier


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
