from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import os
import secrets
from typing import Any

import pytest

from personal_assistant.adapters.persistence.postgres import (
    PostgresPersistence,
    PostgresReminderUnitOfWork,
)
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderCommitOutcomeUnknown,
)
from personal_assistant.application.use_cases.reminder_notifications import (
    DispatchDueReminders,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionTier,
)
from personal_assistant.infrastructure.migrations import apply_migrations

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


@pytest.fixture
def postgres_database() -> tuple[str, str]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for worker PostgreSQL tests")
    psycopg = pytest.importorskip("psycopg")
    schema = f"p4_a4_{secrets.token_hex(6)}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema))
        )
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    psycopg.sql.Identifier(schema)
                )
            )


def principal() -> Principal:
    return Principal.for_test(
        principal_id="worker",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )


def seed(
    persistence: PostgresPersistence,
    actor: Principal,
    *,
    body: object = "private body",
) -> str:
    job = persistence.scheduler.schedule_before_event(
        actor,
        calendar_event_id="cal-1",
        starts_at=NOW,
        channel="telegram",
        recipient="chat-1",
        body="mirror body",
        minutes_before=0,
        idempotency_key="notify-1",
        timezone="America/Bogota",
        source_event_id="source-1",
        payload_fingerprint="a" * 64,
    )
    event = CloudEvent(
        type="notification.requested",
        source="test",
        subject=job.reminder_id,
        tenant_id=actor.tenant_id,
        data={
            "channel": "telegram",
            "recipient": "chat-1",
            "body": body,
        },
    )
    return persistence.outbox.add(
        actor,
        event,
        idempotency_key="outbox-1",
        next_attempt_at=NOW,
    ).id


def approval(actor: Principal, _message: object, dispatch_key: str) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=actor,
        action="notification.send",
        resource=dispatch_key,
        tier=PermissionTier.P5,
    )


@dataclass(slots=True)
class InspectingProvider:
    inspect: Any
    requests: list[NotificationRequest] = field(default_factory=list)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        self.inspect()
        self.requests.append(request)
        return NotificationResult(
            notification_id="telegram:101",
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            provider_message_id=101,
        )


@dataclass(slots=True)
class TransientProvider:
    requests: list[NotificationRequest] = field(default_factory=list)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        self.requests.append(request)
        return NotificationResult(
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            outcome="known-transient",
            provider_code=429,
            retry_after=30,
        )


def test_postgres_confirms_atomic_sending_before_io_and_atomic_published(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    psycopg = pytest.importorskip("psycopg")
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    actor = principal()
    seed(persistence, actor)

    def inspect() -> None:
        with psycopg.connect(dsn) as connection:
            row = connection.execute(
                psycopg.sql.SQL(
                    """
                    SELECT o.dispatch_status, o.attempts,
                           o.payload->>'dispatch_status',
                           s.delivery_status, s.attempts,
                           s.payload->>'delivery_status', s.sent
                    FROM {}.assistant_outbox o
                    JOIN {}.assistant_scheduled_reminders s
                      ON s.tenant_id = o.tenant_id
                     AND s.reminder_id = o.event_payload->>'subject'
                    WHERE o.tenant_id = 'tenant-a'
                    """
                ).format(
                    psycopg.sql.Identifier(schema),
                    psycopg.sql.Identifier(schema),
                )
            ).fetchone()
        assert row == ("sending", 1, "sending", "sending", 1, "sending", True)

    provider = InspectingProvider(inspect)
    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=provider,
        clock=lambda: NOW,
    )
    outcome = dispatcher.dispatch(actor, NOW, approval_provider=approval)

    assert outcome.sent_count == 1
    outbox = persistence.outbox.list_for_tenant(actor)[0]
    scheduler = persistence.scheduler.list_for_tenant(actor)[0]
    assert outbox.dispatch_status.value == "published"
    assert scheduler.delivery_status.value == "published"
    assert scheduler.attempts == outbox.attempts == 1
    assert scheduler.sent is True


class CommitThenDisconnectConnection:
    def __init__(self, connection: Any, error_type: type[Exception]) -> None:
        self._connection = connection
        self._error_type = error_type

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        raise self._error_type("private database diagnostics")


def test_ambiguous_claim_commit_never_authorizes_provider_io(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    psycopg = pytest.importorskip("psycopg")
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    actor = principal()
    seed(persistence, actor)
    provider = InspectingProvider(lambda: pytest.fail("provider must not be called"))

    def uncertain_connection() -> CommitThenDisconnectConnection:
        return CommitThenDisconnectConnection(
            psycopg.connect(dsn), psycopg.OperationalError
        )

    dispatcher = DispatchDueReminders(
        unit_of_work=PostgresReminderUnitOfWork(
            connection_factory=uncertain_connection,
            schema=schema,
        ),
        notifications=provider,
        clock=lambda: NOW,
    )

    with pytest.raises(ReminderCommitOutcomeUnknown, match="outcome is unknown"):
        dispatcher.dispatch(actor, NOW, approval_provider=approval)

    assert provider.requests == []
    assert (
        persistence.outbox.list_for_tenant(actor)[0].dispatch_status.value == "claimed"
    )
    assert (
        persistence.scheduler.list_for_tenant(actor)[0].delivery_status.value
        == "claimed"
    )


def test_postgres_invalid_payload_fails_pre_io_without_attempt_evidence(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    actor = principal()
    seed(persistence, actor, body=None)
    provider = InspectingProvider(lambda: pytest.fail("provider must not be called"))
    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=provider,
        clock=lambda: NOW,
    )

    dispatcher.dispatch(actor, NOW, approval_provider=approval)

    outbox = persistence.outbox.list_for_tenant(actor)[0]
    scheduler = persistence.scheduler.list_for_tenant(actor)[0]
    assert outbox.dispatch_status.value == "failed"
    assert scheduler.delivery_status.value == "failed"
    assert outbox.attempts == scheduler.attempts == 0
    assert outbox.sending_at is scheduler.sending_at is None
    assert provider.requests == []


def test_postgres_transient_retry_is_not_due_before_next_attempt(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    actor = principal()
    seed(persistence, actor)
    provider = TransientProvider()
    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=provider,
        clock=lambda: NOW,
    )

    outcome = dispatcher.dispatch(actor, NOW, approval_provider=approval)

    retry_at = NOW + timedelta(seconds=30)
    assert outcome.sent_count == 0
    assert len(provider.requests) == 1
    outbox = persistence.outbox.list_for_tenant(actor)[0]
    scheduler = persistence.scheduler.list_for_tenant(actor)[0]
    assert outbox.dispatch_status.value == scheduler.delivery_status.value == "pending"
    assert outbox.next_attempt_at == scheduler.next_attempt_at == retry_at
    assert persistence.scheduler.due(actor, NOW) == []
    assert [
        item.reminder_id for item in persistence.scheduler.due(actor, retry_at)
    ] == [scheduler.reminder_id]


def test_postgres_outbox_event_type_filter_and_none_default(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    actor = principal()
    generic = persistence.outbox.add(
        actor,
        CloudEvent(
            type="audit.event.recorded",
            source="other-publisher",
            tenant_id=actor.tenant_id,
        ),
        idempotency_key="generic-1",
        next_attempt_at=NOW,
    )
    notification_id = seed(persistence, actor)

    [notification] = persistence.outbox.claim_due(
        actor,
        NOW,
        limit=1,
        event_type="notification.requested",
    )
    assert notification.id == notification_id
    untouched = next(
        item
        for item in persistence.outbox.list_for_tenant(actor)
        if item.id == generic.id
    )
    assert untouched.dispatch_status.value == "pending"

    [generic_claim] = persistence.outbox.claim_due(
        actor,
        NOW,
        limit=1,
        event_type=None,
    )
    assert generic_claim.id == generic.id
