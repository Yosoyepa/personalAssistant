"""Automated process kill/restart exercise for durable delivery (GAP #7).

Each scenario runs the durable reminder-delivery path in a spawned child
process that kills itself with ``os._exit(99)`` at one instrumented point.
Self-kill is equivalent to an external SIGKILL at that exact line: no cleanup
runs, the database connection drops, and any uncommitted transaction rolls
back, while committed claims and heartbeats stop. The parent process then
performs the "restart" with fresh persistence objects and asserts the
durable-delivery invariants:

- kill before the claim commit: nothing was leased; recovery delivers once;
- kill after the sending commit, before provider I/O: the expired lease is
  swept to ``uncertain`` without automatic resend, and an operator-approved
  retry delivers exactly once;
- kill in the middle of provider I/O: the provider-side effect happened but
  the outcome was never recorded, the system never resends automatically, and
  operator-confirmed reconciliation closes the message as delivered.

These tests require a real PostgreSQL 16 instance via ``TEST_POSTGRES_DSN``
and are skipped in hermetic runs. CI executes them in the
``postgres-integration`` job.
"""

from __future__ import annotations

import multiprocessing
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

import pytest

from personal_assistant.adapters.persistence.postgres import PostgresPersistence
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
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

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
LEASE_SECONDS = 60
KILL_EXIT_CODE = 99
CHILD_JOIN_TIMEOUT_SECONDS = 120


@pytest.fixture
def postgres_database() -> Iterator[tuple[str, str]]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for process recovery tests")
    psycopg = pytest.importorskip("psycopg")
    schema = f"p7_a2_{secrets.token_hex(6)}"
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


def _principal() -> Principal:
    return Principal.for_test(
        principal_id="worker",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )


def _seed(persistence: PostgresPersistence, actor: Principal, *, key_suffix: str) -> str:
    job = persistence.scheduler.schedule_before_event(
        actor,
        calendar_event_id=f"cal-{key_suffix}",
        starts_at=NOW,
        channel="telegram",
        recipient="chat-1",
        body="mirror body",
        minutes_before=0,
        idempotency_key=f"notify-{key_suffix}",
        timezone="America/Bogota",
        source_event_id=f"source-{key_suffix}",
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
            "body": "private body",
        },
    )
    return persistence.outbox.add(
        actor,
        event,
        idempotency_key=f"outbox-{key_suffix}",
        next_attempt_at=NOW,
    ).id


def _approval(actor: Principal, _message: object, dispatch_key: str) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=actor,
        action="notification.send",
        resource=dispatch_key,
        tier=PermissionTier.P5,
    )


def _reconcile_approval(
    actor: Principal, message_id: str, resolution: str
) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=actor,
        action="notification.resolve_uncertain",
        resource=f"{message_id}:{resolution}",
        tier=PermissionTier.P5,
    )


@dataclass(slots=True)
class _RecordingProvider:
    requests: list[NotificationRequest] = field(default_factory=list)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        self.requests.append(request)
        message_id = 100 + len(self.requests)
        return NotificationResult(
            notification_id=f"telegram:{message_id}",
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            provider_message_id=message_id,
        )


def _run_kill_child(target: Any, args: tuple[Any, ...]) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=target, args=args)
    process.start()
    process.join(timeout=CHILD_JOIN_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)
        pytest.fail("kill/restart child did not reach its instrumented kill point")
    assert process.exitcode == KILL_EXIT_CODE, (
        f"child exit code {process.exitcode} != {KILL_EXIT_CODE}: "
        "the process did not die at the instrumented kill point"
    )


def _child_die_at_claim(dsn: str, schema: str) -> None:
    """Run dispatch and die exactly when the claim transaction starts."""
    import os

    actor = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)

    class DieAtClaimOutbox:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def claim_due(self, *args: Any, **kwargs: Any) -> None:
            os._exit(KILL_EXIT_CODE)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    class DieAtClaimTransaction:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self._outbox: DieAtClaimOutbox | None = None

        @property
        def outbox(self) -> DieAtClaimOutbox:
            # The inner transaction assigns .outbox only after BEGIN, so the
            # proxy must wrap it lazily at first access.
            if self._outbox is None:
                self._outbox = DieAtClaimOutbox(self._inner.outbox)
            return self._outbox

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def __enter__(self) -> "DieAtClaimTransaction":
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
            return self._inner.__exit__(exc_type, exc, tb)

    class DieAtClaimUnitOfWork:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def begin(self, principal: Principal) -> DieAtClaimTransaction:
            return DieAtClaimTransaction(self._inner.begin(principal))

    dispatcher = DispatchDueReminders(
        unit_of_work=DieAtClaimUnitOfWork(persistence.reminder_uow),
        notifications=_RecordingProvider(),
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW,
    )
    dispatcher.dispatch(actor, NOW, approval_provider=_approval)
    raise AssertionError("dispatch returned without reaching the kill point")


def _child_die_before_io(dsn: str, schema: str) -> None:
    """Run dispatch and die at provider entry, before any provider I/O."""
    import os

    actor = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)

    class DieBeforeIoNotifications:
        def send(
            self,
            principal: Principal,
            request: NotificationRequest,
            *,
            approval: ApprovalGrant | None = None,
        ) -> None:
            os._exit(KILL_EXIT_CODE)

    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=DieBeforeIoNotifications(),
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW,
    )
    dispatcher.dispatch(actor, NOW, approval_provider=_approval)
    raise AssertionError("dispatch returned without reaching the kill point")


def _child_die_mid_io(dsn: str, schema: str) -> None:
    """Complete the provider-side effect, then die before recording it."""
    import os

    import psycopg

    actor = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)

    class DieMidIoNotifications:
        def send(
            self,
            principal: Principal,
            request: NotificationRequest,
            *,
            approval: ApprovalGrant | None = None,
        ) -> None:
            # The provider-side effect commits in its own connection, so it
            # survives the kill exactly like a real accepted Telegram send.
            with psycopg.connect(dsn, autocommit=True) as connection:
                connection.execute(
                    psycopg.sql.SQL(
                        "INSERT INTO {}.provider_effects (idempotency_key) VALUES (%s)"
                    ).format(psycopg.sql.Identifier(schema)),
                    (request.idempotency_key,),
                )
            os._exit(KILL_EXIT_CODE)

    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=DieMidIoNotifications(),
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW,
    )
    dispatcher.dispatch(actor, NOW, approval_provider=_approval)
    raise AssertionError("dispatch returned without reaching the kill point")


def _provider_effect_count(dsn: str, schema: str) -> int:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            psycopg.sql.SQL("SELECT count(*) FROM {}.provider_effects").format(
                psycopg.sql.Identifier(schema)
            )
        ).fetchone()
    assert row is not None
    return int(row[0])


def _create_provider_effects_table(dsn: str, schema: str) -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL(
                "CREATE TABLE {}.provider_effects (idempotency_key text PRIMARY KEY)"
            ).format(psycopg.sql.Identifier(schema))
        )


def test_kill_before_claim_commit_restart_delivers_exactly_once(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    actor = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    message_id = _seed(persistence, actor, key_suffix="before-claim")

    _run_kill_child(_child_die_at_claim, (dsn, schema))

    after_kill = persistence.outbox.list_for_tenant(actor)
    assert len(after_kill) == 1
    assert after_kill[0].id == message_id
    assert after_kill[0].dispatch_status.value == "pending"
    assert not after_kill[0].claim_token

    restarted = PostgresPersistence(dsn=dsn, schema=schema)
    provider = _RecordingProvider()
    dispatcher = DispatchDueReminders(
        unit_of_work=restarted.reminder_uow,
        notifications=provider,
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW,
    )
    outcome = dispatcher.dispatch(actor, NOW, approval_provider=_approval)

    assert outcome.sent_count == 1
    assert len(provider.requests) == 1
    final = restarted.outbox.list_for_tenant(actor)
    assert len(final) == 1
    assert final[0].dispatch_status.value == "published"
    mirror = restarted.scheduler.list_for_tenant(actor)
    assert len(mirror) == 1
    assert mirror[0].delivery_status.value == "published"


def test_kill_after_sending_commit_sweeps_uncertain_then_retry_delivers_once(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    actor = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    message_id = _seed(persistence, actor, key_suffix="before-io")

    _run_kill_child(_child_die_before_io, (dsn, schema))

    after_kill = persistence.outbox.list_for_tenant(actor)
    assert len(after_kill) == 1
    assert after_kill[0].dispatch_status.value == "sending"
    assert after_kill[0].claim_token

    restarted = PostgresPersistence(dsn=dsn, schema=schema)
    provider = _RecordingProvider()
    dispatcher = DispatchDueReminders(
        unit_of_work=restarted.reminder_uow,
        notifications=provider,
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW + timedelta(seconds=LEASE_SECONDS + 1),
    )
    expired_now = NOW + timedelta(seconds=LEASE_SECONDS + 1)
    sweep_outcome = dispatcher.dispatch(actor, expired_now, approval_provider=_approval)

    assert sweep_outcome.swept_message_ids == (message_id,)
    assert sweep_outcome.sent_count == 0
    assert len(provider.requests) == 0
    swept = restarted.outbox.list_for_tenant(actor)
    assert len(swept) == 1
    assert swept[0].dispatch_status.value == "uncertain"

    restarted_dispatcher = DispatchDueReminders(
        unit_of_work=restarted.reminder_uow,
        notifications=provider,
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW + timedelta(seconds=LEASE_SECONDS + 2),
    )
    retried_now = NOW + timedelta(seconds=LEASE_SECONDS + 2)
    restarted_dispatcher.resolve_uncertain(
        actor,
        message_id,
        resolution="retry",
        now=retried_now,
        approval=_reconcile_approval(actor, message_id, "retry"),
    )
    final_outcome = restarted_dispatcher.dispatch(
        actor, retried_now, approval_provider=_approval
    )

    assert final_outcome.sent_count == 1
    assert len(provider.requests) == 1
    final = restarted.outbox.list_for_tenant(actor)
    assert len(final) == 1
    assert final[0].dispatch_status.value == "published"


def test_kill_mid_provider_io_never_duplicates_the_provider_effect(
    postgres_database: tuple[str, str],
) -> None:
    dsn, schema = postgres_database
    _create_provider_effects_table(dsn, schema)
    actor = _principal()
    persistence = PostgresPersistence(dsn=dsn, schema=schema)
    message_id = _seed(persistence, actor, key_suffix="mid-io")

    _run_kill_child(_child_die_mid_io, (dsn, schema))

    assert _provider_effect_count(dsn, schema) == 1
    after_kill = persistence.outbox.list_for_tenant(actor)
    assert len(after_kill) == 1
    assert after_kill[0].dispatch_status.value == "sending"

    restarted = PostgresPersistence(dsn=dsn, schema=schema)
    provider = _RecordingProvider()
    dispatcher = DispatchDueReminders(
        unit_of_work=restarted.reminder_uow,
        notifications=provider,
        lease_seconds=LEASE_SECONDS,
        clock=lambda: NOW + timedelta(seconds=LEASE_SECONDS + 1),
    )
    expired_now = NOW + timedelta(seconds=LEASE_SECONDS + 1)
    sweep_outcome = dispatcher.dispatch(actor, expired_now, approval_provider=_approval)

    assert sweep_outcome.swept_message_ids == (message_id,)
    assert len(provider.requests) == 0
    assert _provider_effect_count(dsn, schema) == 1
    swept = restarted.outbox.list_for_tenant(actor)
    assert len(swept) == 1
    assert swept[0].dispatch_status.value == "uncertain"

    dispatcher.resolve_uncertain(
        actor,
        message_id,
        resolution="delivered",
        now=NOW + timedelta(seconds=LEASE_SECONDS + 2),
        approval=_reconcile_approval(actor, message_id, "delivered"),
    )

    assert _provider_effect_count(dsn, schema) == 1
    final = restarted.outbox.list_for_tenant(actor)
    assert len(final) == 1
    assert final[0].dispatch_status.value == "published"
    mirror = restarted.scheduler.list_for_tenant(actor)
    assert len(mirror) == 1
    assert mirror[0].delivery_status.value == "published"
