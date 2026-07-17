from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import secrets
from typing import Any

import pytest

from personal_assistant.adapters.persistence.postgres import (
    PostgresPersistence,
    PostgresReminderUnitOfWork,
)
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowStatus
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderCommitOutcomeUnknown,
    ReminderTransactionConflict,
    ReminderTransactionConflictKind,
)
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.reminders.idempotency import ReminderIdempotencyConflict
from personal_assistant.infrastructure.migrations import apply_migrations


TEST_POSTGRES_DSN_ENV = "TEST_POSTGRES_DSN"
NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
BUSINESS_TABLES = {
    "states": ("assistant_workflow_states", "idempotency_key"),
    "calendar": ("assistant_calendar_events", "idempotency_key"),
    "scheduler": ("assistant_scheduled_reminders", "idempotency_key"),
    "events": ("assistant_events", "event_id"),
    "outbox": ("assistant_outbox", "idempotency_key"),
}
TERMINAL_TRACE_TYPES = (
    TraceEventType.tool_called.value,
    TraceEventType.agent_completed.value,
)


class InjectedTraceCrash(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PostgresTestDatabase:
    dsn: str
    schema: str


def _postgres_dsn() -> str:
    dsn = os.environ.get(TEST_POSTGRES_DSN_ENV) or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip(
            f"{TEST_POSTGRES_DSN_ENV} or DATABASE_URL is required for real PostgreSQL tests"
        )
    return dsn


@pytest.fixture
def postgres_database() -> Iterator[PostgresTestDatabase]:
    dsn = _postgres_dsn()
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    schema = f"p3_a5_{secrets.token_hex(6)}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield PostgresTestDatabase(dsn=dsn, schema=schema)
    finally:
        admin.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )
        admin.close()


def _principal(
    *, tenant_id: str = "tenant-a", principal_id: str = "user-a"
) -> Principal:
    return Principal.for_test(
        principal_id=principal_id,
        tenant_id=tenant_id,
        permission_tier=PermissionTier.P5,
    )


def _request(
    principal: Principal,
    source_event_id: str,
    *,
    approved: bool = True,
    text: str = "recuérdame clase el martes a las 17",
) -> ReminderWorkflowInput:
    key = reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel="telegram",
        principal_id=principal.principal_id,
        conversation_id="chat-a",
        source_event_id=source_event_id,
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
        message_id=source_event_id,
        source_event_id=source_event_id,
        conversation_id="chat-a",
        text=text,
        recipient="chat-a",
        now=NOW,
        timezone="America/Bogota",
        idempotency_key=key,
        approval=approval,
    )


def _workflow(
    persistence: PostgresPersistence,
    *,
    traces: Any | None = None,
    unit_of_work: Any | None = None,
) -> ReminderWorkflow:
    return ReminderWorkflow(
        calendar=persistence.calendar,
        scheduler=persistence.scheduler,
        event_store=persistence.event_store,
        outbox=persistence.outbox,
        states=persistence.states,
        traces=traces or persistence.traces,
        unit_of_work=unit_of_work or persistence.reminder_uow,
    )


def _canonical_business_snapshot(
    database: PostgresTestDatabase, tenant_id: str
) -> dict[str, str]:
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    snapshots: dict[str, str] = {}
    with psycopg.connect(database.dsn) as connection:
        for label, (table, order_column) in BUSINESS_TABLES.items():
            query = sql.SQL(
                """
                SELECT COALESCE(jsonb_agg(to_jsonb(rows) ORDER BY {}), '[]'::jsonb)
                FROM (
                    SELECT * FROM {}.{} WHERE tenant_id = %s
                ) AS rows
                """
            ).format(
                sql.Identifier(order_column),
                sql.Identifier(database.schema),
                sql.Identifier(table),
            )
            row = connection.execute(query, (tenant_id,)).fetchone()
            assert row is not None
            snapshots[label] = json.dumps(
                row[0], sort_keys=True, separators=(",", ":"), default=str
            )
    return snapshots


def _business_counts(database: PostgresTestDatabase, tenant_id: str) -> dict[str, int]:
    return {
        label: len(json.loads(payload))
        for label, payload in _canonical_business_snapshot(database, tenant_id).items()
    }


def _terminal_trace_count(
    database: PostgresTestDatabase, tenant_id: str, run_id: str
) -> int:
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    query = sql.SQL(
        """
        SELECT count(*)
        FROM {}.assistant_trace_events
        WHERE tenant_id = %s
          AND run_id = %s
          AND event_type = ANY(%s)
        """
    ).format(sql.Identifier(database.schema))
    with psycopg.connect(database.dsn) as connection:
        row = connection.execute(
            query, (tenant_id, run_id, list(TERMINAL_TRACE_TYPES))
        ).fetchone()
    assert row is not None
    return int(row[0])


def _install_after_trigger(
    database: PostgresTestDatabase, point: str
) -> Callable[[], None]:
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    trigger_specs = {
        "register": ("assistant_workflow_states", "INSERT", ""),
        "resume": (
            "assistant_workflow_states",
            "UPDATE",
            "WHEN (OLD.status = 'waiting_approval' AND NEW.status = 'running')",
        ),
        "calendar": ("assistant_calendar_events", "INSERT", ""),
        "scheduler": ("assistant_scheduled_reminders", "INSERT", ""),
        "event_store": ("assistant_events", "INSERT", ""),
        "outbox": ("assistant_outbox", "INSERT", ""),
        "completed": (
            "assistant_workflow_states",
            "UPDATE",
            "WHEN (NEW.status = 'completed')",
        ),
    }
    table, operation, condition = trigger_specs[point]
    function = f"p3_a5_fail_{point}"
    trigger = f"p3_a5_after_{point}"
    connection = psycopg.connect(database.dsn, autocommit=True)
    connection.execute(
        sql.SQL(
            """
            CREATE FUNCTION {}.{}() RETURNS trigger
            LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'p3_a5 injected fault after write';
            END
            $function$
            """
        ).format(sql.Identifier(database.schema), sql.Identifier(function))
    )
    connection.execute(
        sql.SQL(
            "CREATE TRIGGER {} AFTER {} ON {}.{} FOR EACH ROW {} EXECUTE FUNCTION {}.{}()"
        ).format(
            sql.Identifier(trigger),
            sql.SQL(operation),
            sql.Identifier(database.schema),
            sql.Identifier(table),
            sql.SQL(condition),
            sql.Identifier(database.schema),
            sql.Identifier(function),
        )
    )
    connection.close()

    def remove() -> None:
        cleanup = psycopg.connect(database.dsn, autocommit=True)
        cleanup.execute(
            sql.SQL("DROP TRIGGER IF EXISTS {} ON {}.{}").format(
                sql.Identifier(trigger),
                sql.Identifier(database.schema),
                sql.Identifier(table),
            )
        )
        cleanup.execute(
            sql.SQL("DROP FUNCTION IF EXISTS {}.{}()").format(
                sql.Identifier(database.schema), sql.Identifier(function)
            )
        )
        cleanup.close()

    return remove


@pytest.mark.parametrize(
    "point",
    ["register", "calendar", "scheduler", "event_store", "outbox", "completed"],
)
def test_after_each_postgres_write_failure_preserves_exact_prior_state_and_replays(
    postgres_database: PostgresTestDatabase, point: str
) -> None:
    principal = _principal()
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )
    workflow = _workflow(persistence)
    baseline = workflow.run(principal, _request(principal, "baseline"))
    assert baseline.status == AgentStatus.completed
    before = _canonical_business_snapshot(postgres_database, principal.tenant_id)
    assert _business_counts(postgres_database, principal.tenant_id) == {
        label: 1 for label in BUSINESS_TABLES
    }

    failed_request = _request(principal, f"fault-{point}")
    remove_trigger = _install_after_trigger(postgres_database, point)
    try:
        with pytest.raises(Exception, match="p3_a5 injected fault after write"):
            workflow.run(principal, failed_request)
    finally:
        remove_trigger()

    assert (
        _canonical_business_snapshot(postgres_database, principal.tenant_id) == before
    )
    assert (
        _terminal_trace_count(
            postgres_database,
            principal.tenant_id,
            failed_request.idempotency_key or "",
        )
        == 0
    )

    completed = workflow.run(principal, failed_request)
    replay = workflow.run(principal, failed_request)
    assert completed.status == AgentStatus.completed
    assert replay.status == AgentStatus.completed
    assert replay.reused
    assert replay.calendar_event_id == completed.calendar_event_id
    assert replay.reminder_id == completed.reminder_id
    assert _business_counts(postgres_database, principal.tenant_id) == {
        label: 2 for label in BUSINESS_TABLES
    }
    assert len(persistence.calendar.list_events(principal)) == 2
    assert len(persistence.scheduler.list_for_tenant(principal)) == 2
    assert len(persistence.event_store.list_for_tenant(principal)) == 2
    assert len(persistence.outbox.list_for_tenant(principal)) == 2
    assert len(persistence.states.list_for_tenant(principal)) == 2


def test_failed_postgres_resume_restores_waiting_approval_byte_equivalent(
    postgres_database: PostgresTestDatabase,
) -> None:
    principal = _principal()
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )
    workflow = _workflow(persistence)
    pending_request = _request(principal, "resume", approved=False)
    pending = workflow.run(principal, pending_request)
    assert pending.approval_required
    before = _canonical_business_snapshot(postgres_database, principal.tenant_id)

    remove_trigger = _install_after_trigger(postgres_database, "resume")
    try:
        with pytest.raises(Exception, match="p3_a5 injected fault after write"):
            workflow.run(principal, _request(principal, "resume"))
    finally:
        remove_trigger()

    assert (
        _canonical_business_snapshot(postgres_database, principal.tenant_id) == before
    )
    restored = persistence.states.get_by_idempotency_key(
        principal, pending_request.idempotency_key or ""
    )
    assert restored is not None
    assert restored.status == WorkflowStatus.waiting_approval
    assert _business_counts(postgres_database, principal.tenant_id) == {
        "states": 1,
        "calendar": 0,
        "scheduler": 0,
        "events": 0,
        "outbox": 0,
    }


@dataclass(slots=True)
class CommitVisibleTraceRecorder:
    inner: Any
    database: PostgresTestDatabase
    tenant_id: str
    crash_after_first_terminal: bool = False
    terminal_calls: int = 0

    def write(self, event: TraceEvent) -> None:
        if event.event_type in {
            TraceEventType.tool_called,
            TraceEventType.agent_completed,
        }:
            assert _business_counts(self.database, self.tenant_id) == {
                label: 1 for label in BUSINESS_TABLES
            }
            self.terminal_calls += 1
            self.inner.write(event)
            if self.crash_after_first_terminal and self.terminal_calls == 1:
                raise InjectedTraceCrash("trace sink crashed after durable commit")
            return
        self.inner.write(event)


def test_terminal_traces_observe_the_real_postgres_commit_first(
    postgres_database: PostgresTestDatabase,
) -> None:
    principal = _principal()
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )
    recorder = CommitVisibleTraceRecorder(
        inner=persistence.traces,
        database=postgres_database,
        tenant_id=principal.tenant_id,
    )
    result = _workflow(persistence, traces=recorder).run(
        principal, _request(principal, "trace-order")
    )

    assert result.status == AgentStatus.completed
    assert recorder.terminal_calls == 2


def test_trace_crash_after_commit_replays_without_duplicate_effects(
    postgres_database: PostgresTestDatabase,
) -> None:
    principal = _principal()
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )
    recorder = CommitVisibleTraceRecorder(
        inner=persistence.traces,
        database=postgres_database,
        tenant_id=principal.tenant_id,
        crash_after_first_terminal=True,
    )
    request = _request(principal, "trace-crash")

    with pytest.raises(InjectedTraceCrash, match="after durable commit"):
        _workflow(persistence, traces=recorder).run(principal, request)

    committed = _canonical_business_snapshot(postgres_database, principal.tenant_id)
    replay = _workflow(persistence).run(principal, request)
    assert replay.status == AgentStatus.completed
    assert replay.reused
    assert (
        _canonical_business_snapshot(postgres_database, principal.tenant_id)
        == committed
    )
    assert _business_counts(postgres_database, principal.tenant_id) == {
        label: 1 for label in BUSINESS_TABLES
    }


class CommitThenDisconnectConnection:
    def __init__(self, connection: Any, error_type: type[Exception]) -> None:
        self._connection = connection
        self._error_type = error_type

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        raise self._error_type("simulated disconnect containing private detail")


def test_unknown_commit_outcome_is_sanitized_and_explicit_replay_is_safe(
    postgres_database: PostgresTestDatabase,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    principal = _principal()
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )

    def uncertain_connection() -> CommitThenDisconnectConnection:
        return CommitThenDisconnectConnection(
            psycopg.connect(postgres_database.dsn), psycopg.OperationalError
        )

    uncertain_uow = PostgresReminderUnitOfWork(
        connection_factory=uncertain_connection,
        schema=postgres_database.schema,
    )
    request = _request(principal, "unknown-commit")
    with pytest.raises(ReminderCommitOutcomeUnknown) as raised:
        _workflow(persistence, unit_of_work=uncertain_uow).run(principal, request)

    assert str(raised.value) == "reminder transaction commit outcome is unknown"
    assert "private detail" not in str(raised.value)
    assert _business_counts(postgres_database, principal.tenant_id) == {
        label: 1 for label in BUSINESS_TABLES
    }
    replay = _workflow(persistence).run(principal, request)
    assert replay.status == AgentStatus.completed
    assert replay.reused
    assert _business_counts(postgres_database, principal.tenant_id) == {
        label: 1 for label in BUSINESS_TABLES
    }


def _run_concurrent_reminder(arguments: tuple[str, str, int]) -> dict[str, Any]:
    dsn, schema, attempt = arguments
    principal = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    result = _workflow(persistence).run(
        principal, _request(principal, "multiprocess-shared")
    )
    return {
        "attempt": attempt,
        "pid": os.getpid(),
        "status": result.status.value,
        "calendar_event_id": result.calendar_event_id,
        "reminder_id": result.reminder_id,
    }


def _worker_pid(_: int) -> int:
    return os.getpid()


def _raise_transaction_conflict() -> None:
    raise ReminderTransactionConflict(ReminderTransactionConflictKind.unique_violation)


def _raise_unknown_commit_outcome() -> None:
    raise ReminderCommitOutcomeUnknown


def test_transaction_conflict_round_trips_across_a_process_boundary() -> None:
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_raise_transaction_conflict)
        with pytest.raises(ReminderTransactionConflict) as raised:
            future.result()

    assert raised.value.kind == ReminderTransactionConflictKind.unique_violation


def test_unknown_commit_outcome_round_trips_across_a_process_boundary() -> None:
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_raise_unknown_commit_outcome)
        with pytest.raises(ReminderCommitOutcomeUnknown, match="outcome is unknown"):
            future.result()


def test_same_identity_under_multiprocess_contention_creates_one_logical_reminder(
    postgres_database: PostgresTestDatabase,
) -> None:
    attempts = 24
    with ProcessPoolExecutor(max_workers=4) as pool:
        worker_pids = set(pool.map(_worker_pid, range(16), chunksize=1))
        futures = [
            pool.submit(
                _run_concurrent_reminder,
                (postgres_database.dsn, postgres_database.schema, attempt),
            )
            for attempt in range(attempts)
        ]
        results: list[dict[str, Any]] = []
        conflicts: list[ReminderTransactionConflictKind] = []
        for future in futures:
            try:
                results.append(future.result())
            except ReminderTransactionConflict as conflict:
                conflicts.append(conflict.kind)

    assert len(worker_pids) >= 2
    assert results
    assert {result["status"] for result in results} == {AgentStatus.completed.value}
    assert len({result["calendar_event_id"] for result in results}) == 1
    assert len({result["reminder_id"] for result in results}) == 1
    assert set(conflicts) <= set(ReminderTransactionConflictKind)
    assert _business_counts(postgres_database, "tenant-a") == {
        label: 1 for label in BUSINESS_TABLES
    }
    replay = _workflow(
        PostgresPersistence(dsn=postgres_database.dsn, schema=postgres_database.schema)
    ).run(_principal(), _request(_principal(), "multiprocess-shared"))
    assert replay.status == AgentStatus.completed
    assert replay.reused


def test_distinct_identity_and_tenant_do_not_collide(
    postgres_database: PostgresTestDatabase,
) -> None:
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )
    workflow = _workflow(persistence)
    tenant_a = _principal()
    tenant_b = _principal(tenant_id="tenant-b", principal_id="user-a")

    results = [
        workflow.run(tenant_a, _request(tenant_a, "identity-1")),
        workflow.run(tenant_a, _request(tenant_a, "identity-2")),
        workflow.run(tenant_b, _request(tenant_b, "identity-1")),
    ]

    assert len({result.calendar_event_id for result in results}) == 3
    assert len({result.reminder_id for result in results}) == 3
    assert _business_counts(postgres_database, tenant_a.tenant_id) == {
        label: 2 for label in BUSINESS_TABLES
    }
    assert _business_counts(postgres_database, tenant_b.tenant_id) == {
        label: 1 for label in BUSINESS_TABLES
    }


def test_changed_payload_for_same_identity_remains_a_conflict(
    postgres_database: PostgresTestDatabase,
) -> None:
    principal = _principal()
    persistence = PostgresPersistence(
        dsn=postgres_database.dsn, schema=postgres_database.schema
    )
    workflow = _workflow(persistence)
    workflow.run(principal, _request(principal, "payload-conflict"))
    before = _canonical_business_snapshot(postgres_database, principal.tenant_id)

    with pytest.raises(ReminderIdempotencyConflict):
        workflow.run(
            principal,
            _request(
                principal,
                "payload-conflict",
                text="recuérdame una cita distinta el miércoles a las 10",
            ),
        )

    assert (
        _canonical_business_snapshot(postgres_database, principal.tenant_id) == before
    )
