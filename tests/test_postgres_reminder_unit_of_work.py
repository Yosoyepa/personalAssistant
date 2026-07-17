from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
import secrets
from typing import Any

import psycopg
import pytest

from personal_assistant.adapters.persistence.postgres import (
    PostgresPersistence,
    PostgresReminderTransaction,
    PostgresReminderUnitOfWork,
)
from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderCommitOutcomeUnknown,
    ReminderTransactionConflict,
    ReminderTransactionConflictKind,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.permissions import ApprovalGrant
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.infrastructure.migrations import apply_migrations


class RecordingCursor:
    def __init__(self, connection: RecordingConnection) -> None:
        self.connection = connection

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = None) -> None:
        self.connection.statements.append((statement, params))


class RecordingConnection:
    def __init__(self, *, commit_error: BaseException | None = None) -> None:
        self.statements: list[tuple[str, object]] = []
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


class SqlstateError(RuntimeError):
    def __init__(self, sqlstate: str, secret: str) -> None:
        self.sqlstate = sqlstate
        super().__init__(secret)


def _principal() -> Principal:
    return Principal.for_test(
        principal_id="user-postgres-uow",
        tenant_id="tenant-postgres-uow",
        permission_tier=PermissionTier.P5,
    )


def _factory(connection: RecordingConnection) -> tuple[Callable[[], Any], list[int]]:
    calls: list[int] = []

    def build() -> Any:
        calls.append(1)
        return connection

    return build, calls


def test_one_connection_and_database_are_shared_by_every_transaction_store() -> None:
    connection = RecordingConnection()
    factory, calls = _factory(connection)
    unit_of_work = PostgresReminderUnitOfWork(
        connection_factory=factory, schema="p3_a4_shared"
    )

    with unit_of_work.begin(_principal()) as transaction:
        assert isinstance(transaction, PostgresReminderTransaction)
        databases = {
            id(store._db)  # type: ignore[attr-defined]
            for store in (
                transaction.calendar,
                transaction.scheduler,
                transaction.event_store,
                transaction.outbox,
                transaction.states,
            )
        }
        transaction.commit()
        assert connection.commits == 0

    assert calls == [1]
    assert len(databases) == 1
    assert "SERIALIZABLE" in connection.statements[0][0]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1


@pytest.mark.parametrize("request_commit", [False, True])
def test_exception_rolls_back_even_after_commit_was_requested(
    request_commit: bool,
) -> None:
    connection = RecordingConnection()
    factory, _ = _factory(connection)

    with pytest.raises(RuntimeError, match="application failure"):
        with PostgresReminderUnitOfWork(connection_factory=factory).begin(
            _principal()
        ) as transaction:
            if request_commit:
                transaction.commit()
            raise RuntimeError("application failure")

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_context_without_commit_rolls_back() -> None:
    connection = RecordingConnection()
    factory, _ = _factory(connection)

    with PostgresReminderUnitOfWork(connection_factory=factory).begin(_principal()):
        pass

    assert connection.commits == 0
    assert connection.rollbacks == 1


@pytest.mark.parametrize(
    ("sqlstate", "kind"),
    [
        ("40001", ReminderTransactionConflictKind.serialization_failure),
        ("40P01", ReminderTransactionConflictKind.deadlock_detected),
        ("23505", ReminderTransactionConflictKind.unique_violation),
    ],
)
def test_known_postgres_conflicts_are_distinguishable_and_sanitized(
    sqlstate: str, kind: ReminderTransactionConflictKind
) -> None:
    secret = "postgresql://user:password@host/database SELECT private_payload"
    connection = RecordingConnection(commit_error=SqlstateError(sqlstate, secret))
    factory, _ = _factory(connection)

    with pytest.raises(ReminderTransactionConflict) as captured:
        with PostgresReminderUnitOfWork(connection_factory=factory).begin(
            _principal()
        ) as transaction:
            transaction.commit()

    assert captured.value.kind is kind
    assert secret not in str(captured.value)


def test_ambiguous_commit_is_typed_sanitized_and_never_retried() -> None:
    secret = "postgresql://user:password@host/database private_payload"
    connection = RecordingConnection(commit_error=psycopg.OperationalError(secret))
    factory, calls = _factory(connection)

    with pytest.raises(ReminderCommitOutcomeUnknown) as captured:
        with PostgresReminderUnitOfWork(connection_factory=factory).begin(
            _principal()
        ) as transaction:
            transaction.commit()

    assert secret not in str(captured.value)
    assert calls == [1]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_postgres_persistence_and_runtime_share_the_exact_uow_without_io() -> None:
    connection = RecordingConnection()
    persistence = PostgresPersistence(connection=connection, schema="p3_a4_no_eager_io")

    assert isinstance(persistence.reminder_uow, PostgresReminderUnitOfWork)
    assert connection.statements == []

    container = build_container(
        persistence_backend="postgres",
        database_url="postgresql://not-opened.invalid/database",
        database_schema="p3_a4_runtime",
    )
    assert container.reminder_uow is not None
    assert container.reminder_workflow.unit_of_work is container.reminder_uow


@pytest.fixture
def real_postgres() -> Any:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for real PostgreSQL UoW tests")
    schema = f"p3_a4_{secrets.token_hex(6)}"
    assert schema.startswith("p3_a4_")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema))
        )
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield dsn, schema
    finally:
        assert schema.startswith("p3_a4_")
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(
                    psycopg.sql.Identifier(schema)
                )
            )


def _real_workflow(
    persistence: PostgresPersistence,
    *,
    unit_of_work: PostgresReminderUnitOfWork | None = None,
) -> ReminderWorkflow:
    return ReminderWorkflow(
        calendar=persistence.calendar,
        scheduler=persistence.scheduler,
        event_store=persistence.event_store,
        outbox=persistence.outbox,
        states=persistence.states,
        traces=TraceRecorder(),
        unit_of_work=unit_of_work or persistence.reminder_uow,
    )


def _real_request(principal: Principal) -> ReminderWorkflowInput:
    source_event_id = "postgres-uow-source-1"
    key = reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel="telegram",
        principal_id=principal.principal_id,
        conversation_id="postgres-uow-chat",
        source_event_id=source_event_id,
    )
    approval = ApprovalGrant.issue(
        principal=principal,
        action="calendar.create_event",
        resource=f"{key}:calendar",
        tier=PermissionTier.P3,
    )
    return ReminderWorkflowInput(
        message_id=source_event_id,
        source_event_id=source_event_id,
        conversation_id="postgres-uow-chat",
        text="recuérdame clase el martes a las 17",
        recipient="postgres-uow-chat",
        now=datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
        idempotency_key=key,
        approval=approval,
    )


def test_real_postgres_commit_rollback_and_replay(real_postgres: Any) -> None:
    dsn, schema = real_postgres
    principal = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    state = WorkflowState(
        tenant_id=principal.tenant_id,
        workflow_type="reminder.create",
        status=WorkflowStatus.running,
        idempotency_key="real-rollback",
        payload_fingerprint="a" * 64,
    )

    with persistence.reminder_uow.begin(principal) as transaction:
        transaction.states.register_or_replay(principal, state)
    assert persistence.states.get_by_idempotency_key(principal, "real-rollback") is None

    workflow = _real_workflow(persistence)
    request = _real_request(principal)
    first = workflow.run(principal, request)
    replay = workflow.run(principal, request)

    assert first.status is AgentStatus.completed
    assert replay.status is AgentStatus.completed
    assert replay.calendar_event_id == first.calendar_event_id
    assert replay.reminder_id == first.reminder_id
    assert len(persistence.calendar.list_events(principal)) == 1
    reminders = persistence.scheduler.list_for_tenant(principal)
    assert len(reminders) == 1
    assert len(persistence.event_store.list_for_tenant(principal)) == 1
    messages = persistence.outbox.list_for_tenant(principal)
    assert len(messages) == 1
    assert messages[0].next_attempt_at == reminders[0].notify_at
    states = persistence.states.list_for_tenant(principal)
    assert len(states) == 1
    assert states[0].status is WorkflowStatus.completed


class CommitThenDisconnect:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.commit_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    def commit(self) -> None:
        self.commit_calls += 1
        self.connection.commit()
        raise psycopg.OperationalError("private commit transport detail")


def test_real_commit_unknown_reexecutes_identity_as_completed_replay(
    real_postgres: Any,
) -> None:
    dsn, schema = real_postgres
    principal = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    wrapped_connections: list[CommitThenDisconnect] = []

    def uncertain_factory() -> CommitThenDisconnect:
        wrapped = CommitThenDisconnect(psycopg.connect(dsn))
        wrapped_connections.append(wrapped)
        return wrapped

    uncertain_uow = PostgresReminderUnitOfWork(
        connection_factory=uncertain_factory, schema=schema
    )
    request = _real_request(principal)

    with pytest.raises(ReminderCommitOutcomeUnknown):
        _real_workflow(persistence, unit_of_work=uncertain_uow).run(principal, request)

    replay = _real_workflow(persistence).run(principal, request)
    assert replay.status is AgentStatus.completed
    assert wrapped_connections[0].commit_calls == 1
    assert len(persistence.calendar.list_events(principal)) == 1
    assert len(persistence.scheduler.list_for_tenant(principal)) == 1
    assert len(persistence.event_store.list_for_tenant(principal)) == 1
    assert len(persistence.outbox.list_for_tenant(principal)) == 1


def test_real_concurrency_converges_after_safe_conflict_retry(
    real_postgres: Any,
) -> None:
    dsn, schema = real_postgres
    principal = _principal()
    request = _real_request(principal)

    def run_once(_: int) -> AgentStatus | ReminderTransactionConflictKind:
        persistence = PostgresPersistence(dsn=dsn, schema=schema)
        try:
            return _real_workflow(persistence).run(principal, request).status
        except ReminderTransactionConflict as error:
            return error.kind

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(run_once, range(4)))

    for outcome in outcomes:
        if isinstance(outcome, ReminderTransactionConflictKind):
            assert run_once(0) is AgentStatus.completed

    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    assert AgentStatus.completed in outcomes
    assert len(persistence.calendar.list_events(principal)) == 1
    assert len(persistence.scheduler.list_for_tenant(principal)) == 1
    assert len(persistence.event_store.list_for_tenant(principal)) == 1
    assert len(persistence.outbox.list_for_tenant(principal)) == 1
