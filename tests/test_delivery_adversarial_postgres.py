"""Adversarial PostgreSQL tests for durable notification delivery."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
import os
import secrets
from threading import Barrier, Lock
from typing import Any, Iterator

import pytest

from delivery_adversarial_harness import (
    BlockingNotificationProvider,
    FakeClock,
    FaultInjector,
    InjectedDeliveryCrash,
    ScriptedNotificationProvider,
)
from personal_assistant.adapters.persistence.postgres import (
    PostgresOutbox,
    PostgresPersistence,
    PostgresReminderUnitOfWork,
)
from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
    DeliveryStatus,
)
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage
from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderCommitOutcomeUnknown,
    ReminderTransactionConflict,
)
from personal_assistant.application.use_cases.reminder_notifications import (
    DispatchDueReminders,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionTier,
)
from personal_assistant.infrastructure.migrations import apply_migrations

NOW = datetime(2026, 7, 17, 15, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PostgresSandbox:
    dsn: str
    schema: str


@dataclass(frozen=True, slots=True)
class SeededDelivery:
    message_id: str
    reminder_id: str


@pytest.fixture
def delivery_postgres() -> Iterator[PostgresSandbox]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL delivery tests")
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    schema = f"p4_a5_{secrets.token_hex(6)}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield PostgresSandbox(dsn=dsn, schema=schema)
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def _principal(tenant_id: str = "tenant-a") -> Principal:
    return Principal.for_test(
        principal_id=f"worker-{tenant_id}",
        tenant_id=tenant_id,
        permission_tier=PermissionTier.P5,
    )


def _seed_delivery(
    persistence: PostgresPersistence,
    principal: Principal,
    suffix: str,
) -> SeededDelivery:
    with persistence.reminder_uow.begin(principal) as transaction:
        job = transaction.scheduler.schedule_before_event(
            principal,
            calendar_event_id=f"calendar-{suffix}",
            starts_at=NOW,
            channel="telegram",
            recipient=f"chat-{suffix}",
            body=f"body-{suffix}",
            timezone="America/Bogota",
            source_event_id=f"source-{suffix}",
            payload_fingerprint=secrets.token_hex(32),
            minutes_before=0,
            idempotency_key=f"schedule-{suffix}",
            reminder_id=f"reminder-{suffix}",
        )
        event = CloudEvent(
            id=f"event-{suffix}",
            type="notification.requested",
            source="test",
            subject=job.reminder_id,
            tenant_id=principal.tenant_id,
            time=NOW,
            data={
                "channel": "telegram",
                "recipient": f"chat-{suffix}",
                "body": f"body-{suffix}",
            },
        )
        message = transaction.outbox.add(
            principal,
            event,
            idempotency_key=f"outbox-{suffix}",
            message_id=f"message-{suffix}",
            next_attempt_at=NOW,
        )
        transaction.commit()
    return SeededDelivery(message_id=message.id, reminder_id=job.reminder_id)


def _approval(
    principal: Principal, _message: OutboxMessage, dispatch_key: str
) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=principal,
        action="notification.send",
        resource=dispatch_key,
        tier=PermissionTier.P5,
    )


def _resolution_approval(
    principal: Principal, message_id: str, resolution: str
) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=principal,
        action="notification.resolve_uncertain",
        resource=f"{message_id}:{resolution}",
        tier=PermissionTier.P5,
    )


def _result(
    outcome: str = "success",
    *,
    provider_code: int | None = None,
    retry_after: int | None = None,
    provider_message_id: int = 101,
) -> Callable[[NotificationRequest], NotificationResult]:
    def build(request: NotificationRequest) -> NotificationResult:
        success = outcome == "success"
        return NotificationResult(
            notification_id=(
                f"telegram:{provider_message_id}" if success else None
            ),
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            outcome=outcome,  # type: ignore[arg-type]
            provider_code=provider_code,
            retry_after=retry_after,
            provider_message_id=provider_message_id if success else None,
        )

    return build


def _dispatcher(
    persistence: PostgresPersistence,
    provider: Any,
    clock: FakeClock,
    *,
    owner: str,
    unit_of_work: Any | None = None,
    lease_seconds: int = 60,
) -> DispatchDueReminders:
    return DispatchDueReminders(
        unit_of_work=unit_of_work or persistence.reminder_uow,
        notifications=provider,
        owner=owner,
        claim_limit=1,
        lease_seconds=lease_seconds,
        clock=clock,
    )


def _messages(
    persistence: PostgresPersistence, principal: Principal
) -> dict[str, OutboxMessage]:
    return {
        message.id: message
        for message in persistence.outbox.list_for_tenant(principal)
    }


def _scheduled(persistence: PostgresPersistence, principal: Principal) -> dict[str, Any]:
    return {
        reminder.reminder_id: reminder
        for reminder in persistence.scheduler.list_for_tenant(principal)
    }


def _assert_mirror(
    persistence: PostgresPersistence,
    principal: Principal,
    seeded: SeededDelivery,
    status: DeliveryStatus,
) -> OutboxMessage:
    message = _messages(persistence, principal)[seeded.message_id]
    reminder = _scheduled(persistence, principal)[seeded.reminder_id]
    assert message.dispatch_status is reminder.delivery_status is status
    assert message.attempts == reminder.attempts
    assert message.next_attempt_at == reminder.next_attempt_at
    assert message.sending_at == reminder.sending_at
    assert message.published_at == reminder.published_at
    assert message.last_error == reminder.last_error
    assert reminder.sent is (status is not DeliveryStatus.pending)
    return message


def _error(now: datetime) -> DeliveryError:
    return DeliveryError(
        category=DeliveryErrorCategory.unknown,
        code=DeliveryErrorCode.unknown,
        occurred_at=now,
    )


def _install_mirror_failure(
    database: PostgresSandbox, target_status: DeliveryStatus
) -> Callable[[], None]:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    function = "p4_a5_fail_mirror"
    trigger = "p4_a5_fail_mirror"
    with psycopg.connect(database.dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL(
                """
                CREATE FUNCTION {}.{}() RETURNS trigger
                LANGUAGE plpgsql AS $function$
                BEGIN
                    RAISE EXCEPTION 'p4-a5 mirror fault';
                END
                $function$
                """
            ).format(sql.Identifier(database.schema), sql.Identifier(function))
        )
        connection.execute(
            sql.SQL(
                """
                CREATE TRIGGER {} BEFORE UPDATE
                ON {}.assistant_scheduled_reminders
                FOR EACH ROW WHEN (NEW.delivery_status = {})
                EXECUTE FUNCTION {}.{}()
                """
            ).format(
                sql.Identifier(trigger),
                sql.Identifier(database.schema),
                sql.Literal(target_status.value),
                sql.Identifier(database.schema),
                sql.Identifier(function),
            )
        )

    def remove() -> None:
        with psycopg.connect(database.dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP TRIGGER IF EXISTS {} ON {}.assistant_scheduled_reminders").format(
                    sql.Identifier(trigger), sql.Identifier(database.schema)
                )
            )
            connection.execute(
                sql.SQL("DROP FUNCTION IF EXISTS {}.{}()").format(
                    sql.Identifier(database.schema), sql.Identifier(function)
                )
            )

    return remove


@dataclass(slots=True)
class CommitCounter:
    fail_on: int
    error_type: type[Exception]
    commits: int = 0
    lock: Lock = field(default_factory=Lock)

    def wrap(self, connection: Any) -> Any:
        counter = self

        class AmbiguousCommitConnection:
            def __getattr__(self, name: str) -> Any:
                return getattr(connection, name)

            def commit(self) -> None:
                connection.commit()
                with counter.lock:
                    counter.commits += 1
                    should_fail = counter.commits == counter.fail_on
                if should_fail:
                    raise counter.error_type("private database diagnostics")

        return AmbiguousCommitConnection()


def test_workers_claim_disjoint_sets_and_claim_crash_recovers_without_attempt(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-claim-crash")
    store = PostgresOutbox(dsn=delivery_postgres.dsn, schema=delivery_postgres.schema)
    expected: set[str] = set()
    for index in range(12):
        event = CloudEvent(
            id=f"claim-event-{index}",
            type="notification.requested",
            source="test",
            tenant_id=actor.tenant_id,
            time=NOW,
        )
        expected.add(
            store.add(
                actor,
                event,
                idempotency_key=f"claim-outbox-{index}",
                message_id=f"claim-message-{index}",
            ).id
        )
    start = Barrier(3)

    def claim(owner: str) -> list[OutboxMessage]:
        worker = PostgresOutbox(
            dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
        )
        start.wait()
        return worker.claim_due(actor, NOW, limit=4, owner=owner, lease_seconds=10)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(claim, f"worker-{index}") for index in range(3)]
        batches = [future.result(timeout=10) for future in futures]

    claimed_sets = [{message.id for message in batch} for batch in batches]
    assert all(len(batch) == 4 for batch in batches)
    assert all(
        claimed_sets[left].isdisjoint(claimed_sets[right])
        for left, right in ((0, 1), (0, 2), (1, 2))
    )
    assert set().union(*claimed_sets) == expected
    assert {message.attempts for batch in batches for message in batch} == {0}

    crashed = batches[0][0]
    faults = FaultInjector(armed_at="after_claim")
    with pytest.raises(InjectedDeliveryCrash, match="after_claim"):
        faults.hit("after_claim")

    restarted = PostgresOutbox(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    assert restarted.claim_due(actor, NOW, owner="restart") == []
    recovered = restarted.claim_due(
        actor, NOW + timedelta(seconds=10), limit=12, owner="restart"
    )
    recovered_by_id = {message.id: message for message in recovered}
    assert recovered_by_id[crashed.id].attempts == 0
    assert recovered_by_id[crashed.id].claim_token != crashed.claim_token


def test_two_coordinators_never_double_claim_or_provider_call(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-two-workers")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = [
        _seed_delivery(persistence, actor, f"workers-{index}")
        for index in range(8)
    ]
    provider = ScriptedNotificationProvider(
        [_result(provider_message_id=100 + index) for index in range(8)]
    )
    clock = FakeClock(NOW)
    barrier = Barrier(2)

    def run(owner: str) -> list[str]:
        dispatcher = _dispatcher(persistence, provider, clock, owner=owner)
        claimed: list[str] = []
        barrier.wait(timeout=10)
        for _ in range(12):
            try:
                outcome = dispatcher.dispatch(
                    actor, clock(), approval_provider=_approval
                )
            except ReminderTransactionConflict:
                continue
            claimed.extend(outcome.claimed_message_ids)
        return claimed

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, "coordinator-a")
        second = executor.submit(run, "coordinator-b")
        first_ids = set(first.result(timeout=15))
        second_ids = set(second.result(timeout=15))

    expected = {item.message_id for item in seeded}
    assert first_ids.isdisjoint(second_ids)
    recovery_clock = FakeClock(NOW + timedelta(seconds=60))
    recovery = _dispatcher(
        persistence,
        provider,
        recovery_clock,
        owner="conflict-recovery",
    )
    for _ in seeded:
        recovery.dispatch(
            actor, recovery_clock(), approval_provider=_approval
        )
    assert len(provider.calls) == len(expected)
    assert len({call.idempotency_key for call in provider.calls}) == len(expected)
    called_ids = {
        call.idempotency_key.rsplit(":attempt:", 1)[0] for call in provider.calls
    }
    assert called_ids == expected
    assert {
        message.dispatch_status for message in _messages(persistence, actor).values()
    } <= {DeliveryStatus.published, DeliveryStatus.uncertain}


def test_ambiguous_sending_commit_never_authorizes_provider_io_and_sweeps(
    delivery_postgres: PostgresSandbox,
) -> None:
    psycopg = import_module("psycopg")
    actor = _principal("tenant-ambiguous-sending")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = _seed_delivery(persistence, actor, "ambiguous-sending")
    provider = ScriptedNotificationProvider([_result()])
    counter = CommitCounter(fail_on=2, error_type=psycopg.OperationalError)

    def connection_factory() -> Any:
        return counter.wrap(psycopg.connect(delivery_postgres.dsn))

    uncertain_uow = PostgresReminderUnitOfWork(
        connection_factory=connection_factory,
        schema=delivery_postgres.schema,
    )
    dispatcher = _dispatcher(
        persistence,
        provider,
        FakeClock(NOW),
        owner="ambiguous-worker",
        unit_of_work=uncertain_uow,
    )

    with pytest.raises(ReminderCommitOutcomeUnknown, match="outcome is unknown"):
        dispatcher.dispatch(actor, NOW, approval_provider=_approval)

    assert provider.calls == []
    sending = _assert_mirror(
        persistence, actor, seeded, DeliveryStatus.sending
    )
    assert sending.attempts == 1
    recovery_provider = ScriptedNotificationProvider([])
    recovery = _dispatcher(
        persistence,
        recovery_provider,
        FakeClock(NOW + timedelta(seconds=60)),
        owner="recovery-worker",
    )
    outcome = recovery.dispatch(
        actor, NOW + timedelta(seconds=60), approval_provider=_approval
    )
    assert outcome.swept_message_ids == (seeded.message_id,)
    _assert_mirror(persistence, actor, seeded, DeliveryStatus.uncertain)
    assert recovery_provider.calls == []


@pytest.mark.parametrize("crash_boundary", ["provider-entry", "after-side-effect"])
def test_process_crash_during_provider_is_swept_and_never_auto_resent(
    delivery_postgres: PostgresSandbox,
    crash_boundary: str,
) -> None:
    actor = _principal(f"tenant-{crash_boundary}")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = _seed_delivery(persistence, actor, crash_boundary)

    def crash_after_side_effect(
        _principal_value: Principal, _request: NotificationRequest
    ) -> None:
        if crash_boundary == "after-side-effect":
            raise InjectedDeliveryCrash("provider reset after ambiguous side effect")

    scripts: list[Any] = (
        [InjectedDeliveryCrash("provider reset before response")]
        if crash_boundary == "provider-entry"
        else [_result()]
    )
    provider = ScriptedNotificationProvider(
        scripts,
        observe_before_result=crash_after_side_effect,
    )
    dispatcher = _dispatcher(
        persistence, provider, FakeClock(NOW), owner="crashing-worker"
    )

    with pytest.raises(InjectedDeliveryCrash):
        dispatcher.dispatch(actor, NOW, approval_provider=_approval)

    sending = _assert_mirror(persistence, actor, seeded, DeliveryStatus.sending)
    assert sending.published_at is None
    assert len(provider.calls) == 1

    recovery_provider = ScriptedNotificationProvider([])
    recovery = _dispatcher(
        persistence,
        recovery_provider,
        FakeClock(NOW + timedelta(seconds=60)),
        owner="recovery-worker",
    )
    before_expiry = recovery.dispatch(
        actor, NOW + timedelta(seconds=59), approval_provider=_approval
    )
    assert before_expiry.swept_message_ids == ()
    swept = recovery.dispatch(
        actor, NOW + timedelta(seconds=60), approval_provider=_approval
    )
    assert swept.swept_message_ids == (seeded.message_id,)
    _assert_mirror(persistence, actor, seeded, DeliveryStatus.uncertain)
    assert recovery_provider.calls == []
    assert recovery.dispatch(
        actor, NOW + timedelta(days=30), approval_provider=_approval
    ).claimed_message_ids == ()


def test_known_transient_uses_exact_backoff_and_fails_on_fourth_attempt(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-backoff")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = _seed_delivery(persistence, actor, "backoff")
    provider = ScriptedNotificationProvider(
        [_result("known-transient", provider_code=503) for _ in range(4)]
    )
    clock = FakeClock(NOW)
    dispatcher = _dispatcher(persistence, provider, clock, owner="retry-worker")

    for attempt, delay in enumerate((30, 120, 300), start=1):
        dispatcher.dispatch(actor, clock(), approval_provider=_approval)
        pending = _assert_mirror(
            persistence, actor, seeded, DeliveryStatus.pending
        )
        assert pending.attempts == attempt
        assert pending.next_attempt_at == clock() + timedelta(seconds=delay)
        clock.advance(timedelta(seconds=delay, microseconds=-1))
        assert dispatcher.dispatch(
            actor, clock(), approval_provider=_approval
        ).claimed_message_ids == ()
        clock.advance(timedelta(microseconds=1))

    dispatcher.dispatch(actor, clock(), approval_provider=_approval)
    failed = _assert_mirror(persistence, actor, seeded, DeliveryStatus.failed)
    assert failed.attempts == 4
    assert len(provider.calls) == 4
    assert [call.idempotency_key.rsplit(":", 1)[-1] for call in provider.calls] == [
        "1",
        "2",
        "3",
        "4",
    ]


def test_retry_after_wins_and_permanent_and_unknown_are_terminal(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-outcomes")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    retry = _seed_delivery(persistence, actor, "retry-after")
    permanent = _seed_delivery(persistence, actor, "permanent")
    unknown = _seed_delivery(persistence, actor, "typed-unknown")
    provider = ScriptedNotificationProvider(
        [
            _result("known-transient", provider_code=429, retry_after=91),
            _result("permanent", provider_code=400),
            _result("unknown-outcome", provider_code=504),
        ]
    )
    clock = FakeClock(NOW)
    dispatcher = _dispatcher(persistence, provider, clock, owner="outcome-worker")

    dispatcher.dispatch(actor, clock(), approval_provider=_approval)
    pending = _assert_mirror(persistence, actor, retry, DeliveryStatus.pending)
    assert pending.next_attempt_at == NOW + timedelta(seconds=91)
    dispatcher.dispatch(actor, clock(), approval_provider=_approval)
    _assert_mirror(persistence, actor, permanent, DeliveryStatus.failed)
    dispatcher.dispatch(actor, clock(), approval_provider=_approval)
    uncertain = _assert_mirror(
        persistence, actor, unknown, DeliveryStatus.uncertain
    )
    assert uncertain.last_error is not None
    assert uncertain.last_error.category is DeliveryErrorCategory.unknown
    assert dispatcher.dispatch(
        actor, NOW + timedelta(seconds=90), approval_provider=_approval
    ).claimed_message_ids == ()


def test_concurrent_sweepers_are_disjoint_idempotent_and_ignore_foreign_events(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-sweepers")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = [
        _seed_delivery(persistence, actor, f"sweep-{index}") for index in range(2)
    ]
    crashing_provider = ScriptedNotificationProvider(
        [InjectedDeliveryCrash("crash"), InjectedDeliveryCrash("crash")]
    )
    setup = _dispatcher(
        persistence, crashing_provider, FakeClock(NOW), owner="setup-worker"
    )
    for _ in seeded:
        with pytest.raises(InjectedDeliveryCrash):
            setup.dispatch(actor, NOW, approval_provider=_approval)

    foreign_event = CloudEvent(
        id="foreign-event",
        type="audit.event.recorded",
        source="other-publisher",
        tenant_id=actor.tenant_id,
        time=NOW,
    )
    foreign = persistence.outbox.add(
        actor,
        foreign_event,
        idempotency_key="foreign-outbox",
        message_id="foreign-message",
        next_attempt_at=NOW,
    )
    [foreign_claim] = persistence.outbox.claim_due(
        actor,
        NOW,
        owner="foreign-worker",
        lease_seconds=60,
        event_type=foreign_event.type,
    )
    persistence.outbox.mark_sending(
        actor,
        foreign.id,
        claim_token=foreign_claim.claim_token or "",
        started_at=NOW,
    )

    start = Barrier(2)

    def sweep(owner: str) -> tuple[str, ...]:
        dispatcher = _dispatcher(
            persistence,
            ScriptedNotificationProvider([]),
            FakeClock(NOW + timedelta(seconds=60)),
            owner=owner,
        )
        start.wait(timeout=10)
        for _ in range(3):
            try:
                return dispatcher.dispatch(
                    actor,
                    NOW + timedelta(seconds=60),
                    approval_provider=_approval,
                ).swept_message_ids
            except ReminderTransactionConflict:
                continue
        raise AssertionError("sweeper did not converge after conflicts")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(sweep, "sweeper-a")
        second = executor.submit(sweep, "sweeper-b")
        first_ids = set(first.result(timeout=10))
        second_ids = set(second.result(timeout=10))

    expected = {item.message_id for item in seeded}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == expected
    assert _messages(persistence, actor)[foreign.id].dispatch_status is DeliveryStatus.sending
    empty = _dispatcher(
        persistence,
        ScriptedNotificationProvider([]),
        FakeClock(NOW + timedelta(days=1)),
        owner="idempotency-sweeper",
    ).dispatch(actor, NOW + timedelta(days=1), approval_provider=_approval)
    assert empty.swept_message_ids == ()


def test_mirror_failure_rolls_back_terminal_and_restart_sweeps_without_resend(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-mirror-rollback")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = _seed_delivery(persistence, actor, "mirror-rollback")
    provider = ScriptedNotificationProvider([_result()])
    dispatcher = _dispatcher(
        persistence, provider, FakeClock(NOW), owner="mirror-worker"
    )
    remove_failure = _install_mirror_failure(
        delivery_postgres, DeliveryStatus.published
    )
    try:
        with pytest.raises(Exception, match="p4-a5 mirror fault"):
            dispatcher.dispatch(actor, NOW, approval_provider=_approval)
    finally:
        remove_failure()

    sending = _assert_mirror(persistence, actor, seeded, DeliveryStatus.sending)
    assert sending.published_at is None
    assert len(provider.calls) == 1
    recovery_provider = ScriptedNotificationProvider([])
    outcome = _dispatcher(
        persistence,
        recovery_provider,
        FakeClock(NOW + timedelta(seconds=60)),
        owner="mirror-recovery",
    ).dispatch(actor, NOW + timedelta(seconds=60), approval_provider=_approval)
    assert outcome.swept_message_ids == (seeded.message_id,)
    _assert_mirror(persistence, actor, seeded, DeliveryStatus.uncertain)
    assert recovery_provider.calls == []


def test_missing_mirror_after_io_keeps_canonical_outbox_terminal(
    delivery_postgres: PostgresSandbox,
) -> None:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    actor = _principal("tenant-missing-mirror")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = _seed_delivery(persistence, actor, "missing-mirror")

    def remove_scheduler_after_sending(
        _principal_value: Principal, _request: NotificationRequest
    ) -> None:
        with psycopg.connect(delivery_postgres.dsn) as connection:
            connection.execute(
                sql.SQL(
                    "DELETE FROM {}.assistant_scheduled_reminders "
                    "WHERE tenant_id = %s AND reminder_id = %s"
                ).format(sql.Identifier(delivery_postgres.schema)),
                (actor.tenant_id, seeded.reminder_id),
            )

    provider = ScriptedNotificationProvider(
        [_result()], observe_before_result=remove_scheduler_after_sending
    )
    outcome = _dispatcher(
        persistence, provider, FakeClock(NOW), owner="missing-mirror-worker"
    ).dispatch(actor, NOW, approval_provider=_approval)

    assert outcome.sent_count == 1
    assert _messages(persistence, actor)[seeded.message_id].dispatch_status is DeliveryStatus.published
    assert persistence.scheduler.list_for_tenant(actor) == []


def test_manual_resolution_requires_exact_p5_approval_and_mirrors_both_paths(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-resolution")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    delivered = _seed_delivery(persistence, actor, "resolve-delivered")
    retry = _seed_delivery(persistence, actor, "resolve-retry")
    provider = ScriptedNotificationProvider(
        [_result("unknown-outcome"), _result("unknown-outcome")]
    )
    dispatcher = _dispatcher(
        persistence, provider, FakeClock(NOW), owner="resolution-worker"
    )
    dispatcher.dispatch(actor, NOW, approval_provider=_approval)
    dispatcher.dispatch(actor, NOW, approval_provider=_approval)

    with pytest.raises(AssistantError) as missing_approval:
        dispatcher.resolve_uncertain(
            actor,
            retry.message_id,
            resolution="retry",
            now=NOW + timedelta(minutes=1),
        )
    assert missing_approval.value.code is ErrorCode.PERMISSION_DENIED
    _assert_mirror(persistence, actor, retry, DeliveryStatus.uncertain)

    p3_actor = Principal.for_test(
        principal_id="p3-operator",
        tenant_id=actor.tenant_id,
        permission_tier=PermissionTier.P3,
    )
    p3_grant = ApprovalGrant.issue(
        principal=p3_actor,
        action="notification.resolve_uncertain",
        resource=f"{retry.message_id}:retry",
        tier=PermissionTier.P3,
    )
    with pytest.raises(AssistantError) as insufficient_tier:
        dispatcher.resolve_uncertain(
            p3_actor,
            retry.message_id,
            resolution="retry",
            now=NOW + timedelta(minutes=1),
            approval=p3_grant,
        )
    assert insufficient_tier.value.code is ErrorCode.PERMISSION_DENIED
    _assert_mirror(persistence, actor, retry, DeliveryStatus.uncertain)

    wrong = _resolution_approval(actor, retry.message_id, "delivered")
    with pytest.raises(AssistantError) as rejected:
        dispatcher.resolve_uncertain(
            actor,
            retry.message_id,
            resolution="retry",
            now=NOW + timedelta(minutes=1),
            approval=wrong,
        )
    assert rejected.value.code is ErrorCode.PERMISSION_DENIED
    _assert_mirror(persistence, actor, retry, DeliveryStatus.uncertain)

    dispatcher.resolve_uncertain(
        actor,
        delivered.message_id,
        resolution="delivered",
        now=NOW + timedelta(minutes=1),
        approval=_resolution_approval(actor, delivered.message_id, "delivered"),
    )
    published = _assert_mirror(
        persistence, actor, delivered, DeliveryStatus.published
    )
    assert published.published_at == NOW + timedelta(minutes=1)
    dispatcher.resolve_uncertain(
        actor,
        retry.message_id,
        resolution="retry",
        now=NOW + timedelta(minutes=2),
        approval=_resolution_approval(actor, retry.message_id, "retry"),
    )
    pending = _assert_mirror(persistence, actor, retry, DeliveryStatus.pending)
    assert pending.next_attempt_at == NOW + timedelta(minutes=2)


def test_tenant_and_token_fencing_survive_restart(
    delivery_postgres: PostgresSandbox,
) -> None:
    tenant_a = _principal("tenant-fence-a")
    tenant_b = _principal("tenant-fence-b")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = _seed_delivery(persistence, tenant_a, "tenant-fence")
    [first] = persistence.outbox.claim_due(
        tenant_a, NOW, owner="first", lease_seconds=10
    )

    with pytest.raises(AssistantError) as cross_tenant:
        persistence.outbox.mark_sending(
            tenant_b,
            seeded.message_id,
            claim_token=first.claim_token or "",
            started_at=NOW,
        )
    assert cross_tenant.value.code is ErrorCode.NOT_FOUND

    restarted = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    [second] = restarted.outbox.claim_due(
        tenant_a,
        NOW + timedelta(seconds=10),
        owner="second",
        lease_seconds=10,
    )
    with pytest.raises(AssistantError) as stale_token:
        restarted.outbox.mark_sending(
            tenant_a,
            seeded.message_id,
            claim_token=first.claim_token or "",
            started_at=NOW + timedelta(seconds=10),
        )
    assert stale_token.value.code is ErrorCode.PERMISSION_DENIED
    sending = restarted.outbox.mark_sending(
        tenant_a,
        seeded.message_id,
        claim_token=second.claim_token or "",
        started_at=NOW + timedelta(seconds=10),
    )
    assert sending.attempts == 1
    assert restarted.outbox.list_for_tenant(tenant_b) == []


def test_blocked_provider_claim_one_prevents_aged_queued_claims_and_double_send(
    delivery_postgres: PostgresSandbox,
) -> None:
    actor = _principal("tenant-blocked-provider")
    persistence = PostgresPersistence(
        dsn=delivery_postgres.dsn, schema=delivery_postgres.schema
    )
    seeded = [
        _seed_delivery(persistence, actor, f"blocked-{index}") for index in range(3)
    ]
    clock = FakeClock(NOW)
    first_provider = BlockingNotificationProvider(_result(provider_message_id=201))
    first_dispatcher = _dispatcher(
        persistence,
        first_provider,
        clock,
        owner="slow-worker",
        lease_seconds=60,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            first_dispatcher.dispatch,
            actor,
            clock(),
            approval_provider=_approval,
        )
        assert first_provider.entered.wait(timeout=10)
        states = _messages(persistence, actor)
        sending_ids = {
            message.id
            for message in states.values()
            if message.dispatch_status is DeliveryStatus.sending
        }
        pending_ids = {
            message.id
            for message in states.values()
            if message.dispatch_status is DeliveryStatus.pending
        }
        assert len(sending_ids) == 1
        assert len(pending_ids) == 2
        assert all(
            states[message_id].claim_owner is None for message_id in pending_ids
        )
        in_flight_id = next(iter(sending_ids))
        in_flight = states[in_flight_id]
        in_flight_seed = next(
            item for item in seeded if item.message_id == in_flight_id
        )
        mirrored = _scheduled(persistence, actor)[in_flight_seed.reminder_id]
        assert in_flight.dispatch_status is DeliveryStatus.sending
        assert mirrored.delivery_status is DeliveryStatus.sending
        assert in_flight.published_at is mirrored.published_at is None
        assert in_flight.dispatch_status is not DeliveryStatus.published
        assert mirrored.delivery_status is not DeliveryStatus.published

        clock.advance(timedelta(seconds=60))
        second_provider = ScriptedNotificationProvider(
            [_result(provider_message_id=202), _result(provider_message_id=203)]
        )
        second_dispatcher = _dispatcher(
            persistence, second_provider, clock, owner="peer-worker"
        )
        first_peer = second_dispatcher.dispatch(
            actor, clock(), approval_provider=_approval
        )
        assert set(first_peer.swept_message_ids) == sending_ids
        assert set(first_peer.claimed_message_ids).issubset(pending_ids)
        assert len(second_provider.calls) == 1
        first_provider.release.set()
        with pytest.raises(AssistantError):
            future.result(timeout=10)

    second_dispatcher.dispatch(actor, clock(), approval_provider=_approval)
    assert len(second_provider.calls) == 2
    all_keys = [call.idempotency_key for call in first_provider.calls]
    all_keys.extend(call.idempotency_key for call in second_provider.calls)
    assert len(all_keys) == len(set(all_keys)) == 3
    terminal = _messages(persistence, actor)
    assert {
        terminal[item.message_id].dispatch_status for item in seeded
    } == {DeliveryStatus.published, DeliveryStatus.uncertain}
