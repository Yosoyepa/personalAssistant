from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from datetime import UTC, datetime
import os
from pathlib import Path
import secrets
from typing import Any

import pytest

from personal_assistant.adapters.persistence import postgres
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.ports.calendar import CalendarEventRequest
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.reminders.idempotency import (
    ReminderIdempotencyIdentity,
    reminder_effect_ids,
)
from personal_assistant.infrastructure.migrations import apply_migrations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "personal_assistant"
    / "infrastructure"
    / "migrations"
    / "sql"
    / "0002_reminder_identity_constraints.sql"
)
TEST_POSTGRES_DSN_ENV = "TEST_POSTGRES_DSN"


def _postgres_dsn() -> str:
    dsn = os.environ.get(TEST_POSTGRES_DSN_ENV) or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip(
            f"{TEST_POSTGRES_DSN_ENV} or DATABASE_URL is required for real PostgreSQL tests"
        )
    return dsn


@pytest.fixture
def postgres_schema() -> Iterator[tuple[Any, str]]:
    dsn = _postgres_dsn()
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    schema = f"p3_a3_{secrets.token_hex(6)}"
    connection = psycopg.connect(dsn, autocommit=True)
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    connection.execute(
        sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema))
    )
    try:
        yield connection, schema
    finally:
        connection.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )
        connection.close()


def _create_alpha_tables(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE assistant_events (
            tenant_id TEXT NOT NULL,
            event_id TEXT NOT NULL
        );
        CREATE TABLE assistant_outbox (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            message_id TEXT NOT NULL,
            event_id TEXT NOT NULL
        );
        CREATE TABLE assistant_calendar_events (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            event_id TEXT NOT NULL
        );
        CREATE TABLE assistant_scheduled_reminders (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            reminder_id TEXT NOT NULL
        );
        """
    )


def _run_migration(connection: Any) -> None:
    connection.execute(MIGRATION_PATH.read_text(encoding="utf-8"))


def test_real_postgres_constraints_are_idempotent_and_tenant_scoped(
    postgres_schema: tuple[Any, str],
) -> None:
    connection, schema = postgres_schema
    psycopg = pytest.importorskip("psycopg")
    _create_alpha_tables(connection)

    _run_migration(connection)
    _run_migration(connection)

    expected_indexes = {
        "assistant_events_tenant_event_id_uidx",
        "assistant_outbox_tenant_idempotency_key_uidx",
        "assistant_outbox_tenant_message_id_uidx",
        "assistant_outbox_tenant_event_id_uidx",
        "assistant_calendar_events_tenant_idempotency_key_uidx",
        "assistant_calendar_events_tenant_event_id_uidx",
        "assistant_scheduled_reminders_tenant_idempotency_key_uidx",
        "assistant_scheduled_reminders_tenant_reminder_id_uidx",
    }
    rows = connection.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = %s
        """,
        (schema,),
    ).fetchall()
    assert expected_indexes <= {row[0] for row in rows}

    connection.execute("INSERT INTO assistant_events VALUES ('tenant-a', 'evt-1')")
    connection.execute("INSERT INTO assistant_events VALUES ('tenant-b', 'evt-1')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute("INSERT INTO assistant_events VALUES ('tenant-a', 'evt-1')")

    connection.execute(
        "INSERT INTO assistant_calendar_events VALUES ('tenant-a', 'key-1', 'cal-1')"
    )
    connection.execute(
        "INSERT INTO assistant_calendar_events VALUES ('tenant-b', 'key-1', 'cal-1')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO assistant_calendar_events VALUES "
            "('tenant-a', 'key-1', 'cal-2')"
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO assistant_calendar_events VALUES "
            "('tenant-a', 'key-2', 'cal-1')"
        )

    connection.execute(
        "INSERT INTO assistant_scheduled_reminders VALUES "
        "('tenant-a', 'key-1', 'rem-1')"
    )
    connection.execute(
        "INSERT INTO assistant_scheduled_reminders VALUES "
        "('tenant-b', 'key-1', 'rem-1')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO assistant_scheduled_reminders VALUES "
            "('tenant-a', 'key-1', 'rem-2')"
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO assistant_scheduled_reminders VALUES "
            "('tenant-a', 'key-2', 'rem-1')"
        )

    connection.execute(
        "INSERT INTO assistant_outbox VALUES "
        "('tenant-a', 'key-1', 'out-1', 'notification-1')"
    )
    connection.execute(
        "INSERT INTO assistant_outbox VALUES "
        "('tenant-b', 'key-1', 'out-1', 'notification-1')"
    )
    for row in (
        "('tenant-a', 'key-1', 'out-2', 'notification-2')",
        "('tenant-a', 'key-2', 'out-1', 'notification-2')",
        "('tenant-a', 'key-2', 'out-2', 'notification-1')",
    ):
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(f"INSERT INTO assistant_outbox VALUES {row}")


def test_real_postgres_preflight_preserves_duplicate_alpha_rows(
    postgres_schema: tuple[Any, str],
) -> None:
    connection, _ = postgres_schema
    psycopg = pytest.importorskip("psycopg")
    _create_alpha_tables(connection)
    connection.execute(
        "INSERT INTO assistant_calendar_events VALUES "
        "('tenant-alpha', 'duplicate-key', 'cal-old-1'), "
        "('tenant-alpha', 'duplicate-key', 'cal-old-2')"
    )

    with pytest.raises(psycopg.errors.UniqueViolation, match="alpha data"):
        _run_migration(connection)

    row = connection.execute(
        "SELECT count(*) FROM assistant_calendar_events"
    ).fetchone()
    assert row is not None and row[0] == 2
    indexes = connection.execute(
        """
        SELECT count(*)
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND indexname LIKE 'assistant_%_tenant_%_uidx'
        """
    ).fetchone()
    assert indexes is not None and indexes[0] == 0


def test_real_postgres_effect_writes_replay_once_under_concurrency(
    postgres_schema: tuple[Any, str],
) -> None:
    connection, schema = postgres_schema
    dsn = _postgres_dsn()
    apply_migrations(dsn=dsn, schema=schema)

    principal = Principal.for_test(
        principal_id="user-postgres",
        tenant_id="tenant-postgres",
        permission_tier=PermissionTier.P5,
    )
    identity = ReminderIdempotencyIdentity(
        tenant_id=principal.tenant_id,
        channel="telegram",
        principal_id=principal.principal_id,
        conversation_id="chat-postgres",
        source_event_id="event-postgres",
    )
    key = identity.idempotency_key
    ids = reminder_effect_ids(identity)
    starts_at = datetime(2026, 7, 19, 18, tzinfo=UTC)
    occurred_at = datetime(2026, 7, 17, 18, tzinfo=UTC)
    calendar_request = CalendarEventRequest(
        event_id=ids.calendar_event_id,
        title="Postgres estable",
        starts_at=starts_at,
        timezone="UTC",
        idempotency_key=f"{key}:calendar",
        source_event_id=identity.source_event_id,
        payload_fingerprint="a" * 64,
    )
    approval = ApprovalGrant.issue(
        principal=principal,
        action="calendar.create_event",
        resource=calendar_request.idempotency_key,
        tier=PermissionTier.P3,
    )
    reminder_event = CloudEvent(
        id=ids.reminder_created_event_id,
        type="reminder.created",
        source="test.postgres",
        subject=ids.reminder_id,
        tenant_id=principal.tenant_id,
        correlation_id=key,
        source_event_id=identity.source_event_id,
        payload_fingerprint="a" * 64,
        timezone="UTC",
        data={"calendar_event_id": ids.calendar_event_id},
        time=occurred_at,
    )
    notification_event = CloudEvent(
        id=ids.notification_requested_event_id,
        type="notification.requested",
        source="test.postgres",
        subject=ids.reminder_id,
        tenant_id=principal.tenant_id,
        correlation_id=key,
        causation_id=ids.reminder_created_event_id,
        source_event_id=identity.source_event_id,
        payload_fingerprint="a" * 64,
        timezone="UTC",
        data={"reminder_id": ids.reminder_id},
        time=occurred_at,
    )

    def write_all(_: int) -> tuple[str, str, str, str, str]:
        persistence = postgres.PostgresPersistence(dsn=dsn, schema=schema)
        calendar_result = persistence.calendar.create_event(
            principal, calendar_request, approval=approval
        )
        reminder = persistence.scheduler.schedule_before_event(
            principal,
            calendar_event_id=calendar_result.event_id,
            starts_at=starts_at,
            channel="telegram",
            recipient="chat-postgres",
            body="Postgres estable",
            timezone="UTC",
            source_event_id=identity.source_event_id,
            payload_fingerprint="a" * 64,
            idempotency_key=f"{key}:notify",
            reminder_id=ids.reminder_id,
        )
        stored_event = persistence.event_store.append(principal, reminder_event)
        message = persistence.outbox.add(
            principal,
            notification_event,
            idempotency_key=f"{key}:outbox",
            message_id=ids.outbox_message_id,
        )
        return (
            calendar_result.event_id,
            reminder.reminder_id,
            stored_event.id,
            message.event.id,
            message.id,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write_all, range(16)))

    assert set(results) == {
        (
            ids.calendar_event_id,
            ids.reminder_id,
            ids.reminder_created_event_id,
            ids.notification_requested_event_id,
            ids.outbox_message_id,
        )
    }
    persisted = postgres.PostgresPersistence(dsn=dsn, schema=schema)
    assert len(persisted.calendar.list_events(principal)) == 1
    assert len(persisted.scheduler.list_for_tenant(principal)) == 1
    assert len(persisted.event_store.list_for_tenant(principal)) == 1
    assert len(persisted.outbox.list_for_tenant(principal)) == 1

    changed = calendar_request.model_copy(update={"title": "Payload distinto"})
    with pytest.raises(AssistantError) as conflict:
        persisted.calendar.create_event(principal, changed, approval=approval)
    assert conflict.value.code == ErrorCode.CONFLICT
