from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
import secrets
from threading import Barrier
from typing import Any, Iterator

import pytest

from personal_assistant.adapters.persistence.postgres import (
    PostgresOutbox,
    _PostgresDatabase,
)
from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
    DeliveryStatus,
)
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.migrations import apply_migrations


NOW = datetime(2026, 7, 17, 15, tzinfo=UTC)


@pytest.fixture
def real_postgres_claims() -> Iterator[tuple[str, str]]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for real PostgreSQL claim tests")
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    schema = f"p4_a2_{secrets.token_hex(6)}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def _principal(tenant_id: str) -> Principal:
    return Principal.for_test(
        principal_id=f"worker-{tenant_id}",
        tenant_id=tenant_id,
        permission_tier=PermissionTier.P5,
    )


def _add_message(
    store: PostgresOutbox,
    principal: Principal,
    suffix: str,
    *,
    next_attempt_at: datetime | None = None,
) -> str:
    event = CloudEvent(
        id=f"event-{suffix}",
        type="notification.requested",
        source="test",
        tenant_id=principal.tenant_id,
        time=NOW,
        data={"suffix": suffix},
    )
    return store.add(
        principal,
        event,
        idempotency_key=f"outbox-{suffix}",
        message_id=f"message-{suffix}",
        next_attempt_at=next_attempt_at,
    ).id


def _delivery_error(*, category: DeliveryErrorCategory | None = None) -> DeliveryError:
    return DeliveryError(
        category=category or DeliveryErrorCategory.network,
        code=DeliveryErrorCode.provider_unavailable,
        provider_code=503,
        occurred_at=NOW + timedelta(seconds=2),
    )


def test_real_postgres_multiworker_claims_are_disjoint_and_tenant_scoped(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    tenant_a = _principal("tenant-a")
    tenant_b = _principal("tenant-b")
    seed = PostgresOutbox(dsn=dsn, schema=schema)
    expected_a = {_add_message(seed, tenant_a, f"a-{index}") for index in range(8)}
    expected_b = {_add_message(seed, tenant_b, f"b-{index}") for index in range(2)}
    barrier = Barrier(2)

    def claim(owner: str) -> list[Any]:
        worker = PostgresOutbox(dsn=dsn, schema=schema)
        barrier.wait()
        return worker.claim_due(tenant_a, NOW, limit=4, owner=owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, owner) for owner in ("worker-1", "worker-2")]
        first, second = (future.result(timeout=10) for future in futures)

    first_ids = {message.id for message in first}
    second_ids = {message.id for message in second}
    assert len(first) == len(second) == 4
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == expected_a
    assert {message.claim_owner for message in first} == {"worker-1"}
    assert {message.claim_owner for message in second} == {"worker-2"}
    assert {message.attempts for message in first + second} == {0}
    assert all(message.claim_token for message in first + second)
    assert len({message.claim_token for message in first + second}) == 8
    assert all(
        message.claimed_until == NOW + timedelta(seconds=30)
        for message in first + second
    )

    tenant_b_claims = seed.claim_due(
        tenant_b, NOW, limit=10, owner="worker-b", lease_seconds=30
    )
    assert {message.id for message in tenant_b_claims} == expected_b


def test_real_postgres_due_activation_expired_lease_and_unsafe_states(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    principal = _principal("tenant-state")
    store = PostgresOutbox(dsn=dsn, schema=schema)
    due_id = _add_message(store, principal, "due")
    future_id = _add_message(
        store, principal, "future", next_attempt_at=NOW + timedelta(minutes=5)
    )

    [first] = store.claim_due(
        principal, NOW, limit=10, owner="worker-1", lease_seconds=30
    )
    assert first.id == due_id
    assert (
        store.claim_due(principal, NOW + timedelta(seconds=29), owner="worker-2") == []
    )

    [reclaimed] = store.claim_due(
        principal, NOW + timedelta(seconds=30), owner="worker-2"
    )
    assert reclaimed.id == due_id
    assert first.attempts == 0
    assert reclaimed.attempts == 0
    assert reclaimed.claim_token != first.claim_token

    sending = store.mark_sending(
        principal,
        due_id,
        claim_token=reclaimed.claim_token or "",
        started_at=NOW + timedelta(seconds=31),
    )
    assert sending.dispatch_status is DeliveryStatus.sending
    assert sending.attempts == 1
    with pytest.raises(AssistantError) as duplicate_sending:
        store.mark_sending(
            principal,
            due_id,
            claim_token=sending.claim_token or "",
            started_at=NOW + timedelta(seconds=32),
        )
    assert duplicate_sending.value.code is ErrorCode.CONFLICT
    assert {
        message.id: message.attempts for message in store.list_for_tenant(principal)
    }[due_id] == 1
    assert (
        store.claim_due(principal, NOW + timedelta(days=1), owner="worker-3")[0].id
        == future_id
    )
    assert all(
        message.id != due_id
        for message in store.claim_due(
            principal, NOW + timedelta(days=2), owner="worker-4"
        )
    )

    uncertain = store.mark_uncertain(
        principal,
        due_id,
        claim_token=sending.claim_token or "",
        error=_delivery_error(),
    )
    assert uncertain.dispatch_status is DeliveryStatus.uncertain
    assert all(
        message.id != due_id
        for message in store.claim_due(
            principal, NOW + timedelta(days=3), owner="worker-5"
        )
    )


def test_real_postgres_claim_skips_a_row_locked_by_another_connection(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    principal = _principal("tenant-locked")
    store = PostgresOutbox(dsn=dsn, schema=schema)
    locked_id = _add_message(store, principal, "locked-1")
    available_id = _add_message(store, principal, "locked-2")

    blocker = psycopg.connect(dsn)
    try:
        blocker.execute("SET LOCAL statement_timeout = '5s'")
        blocker.execute(
            sql.SQL(
                "SELECT message_id FROM {}.assistant_outbox "
                "WHERE tenant_id = %s AND message_id = %s FOR UPDATE"
            ).format(sql.Identifier(schema)),
            (principal.tenant_id, locked_id),
        )

        [claimed] = store.claim_due(
            principal, NOW, limit=2, owner="skip-worker", lease_seconds=30
        )
        assert claimed.id == available_id
    finally:
        blocker.rollback()
        blocker.close()

    [formerly_locked] = store.claim_due(
        principal, NOW, limit=2, owner="next-worker", lease_seconds=30
    )
    assert formerly_locked.id == locked_id


def test_real_postgres_transition_is_tenant_guarded(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    tenant_a = _principal("tenant-transition-a")
    tenant_b = _principal("tenant-transition-b")
    store = PostgresOutbox(dsn=dsn, schema=schema)
    message_id = _add_message(store, tenant_a, "tenant-transition")
    [claimed] = store.claim_due(
        tenant_a, NOW, owner="tenant-a-worker", lease_seconds=30
    )

    with pytest.raises(AssistantError) as cross_tenant:
        store.mark_sending(
            tenant_b,
            message_id,
            claim_token=claimed.claim_token or "",
            started_at=NOW + timedelta(seconds=1),
        )

    assert cross_tenant.value.code is ErrorCode.NOT_FOUND
    assert store.list_for_tenant(tenant_b) == []
    [unchanged] = store.list_for_tenant(tenant_a)
    assert unchanged.dispatch_status is DeliveryStatus.claimed
    assert unchanged.claim_token == claimed.claim_token
    assert unchanged.claim_owner == claimed.claim_owner
    assert unchanged.claimed_until == claimed.claimed_until
    assert unchanged.attempts == claimed.attempts == 0


def test_real_postgres_published_failed_and_uncertain_are_never_reclaimed(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    principal = _principal("tenant-terminal")
    store = PostgresOutbox(dsn=dsn, schema=schema)
    published_id = _add_message(store, principal, "terminal-published")
    failed_id = _add_message(store, principal, "terminal-failed")
    uncertain_id = _add_message(store, principal, "terminal-uncertain")
    claimed = store.claim_due(principal, NOW, owner="terminal-worker", lease_seconds=30)
    by_id = {message.id: message for message in claimed}

    sending_published = store.mark_sending(
        principal,
        published_id,
        claim_token=by_id[published_id].claim_token or "",
        started_at=NOW + timedelta(seconds=1),
    )
    store.mark_published(
        principal,
        published_id,
        claim_token=sending_published.claim_token or "",
        published_at=NOW + timedelta(seconds=2),
    )

    sending_failed = store.mark_sending(
        principal,
        failed_id,
        claim_token=by_id[failed_id].claim_token or "",
        started_at=NOW + timedelta(seconds=1),
    )
    store.mark_failed(
        principal,
        failed_id,
        claim_token=sending_failed.claim_token or "",
        error=_delivery_error(category=DeliveryErrorCategory.rejected),
    )

    sending_uncertain = store.mark_sending(
        principal,
        uncertain_id,
        claim_token=by_id[uncertain_id].claim_token or "",
        started_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(AssistantError) as wrong_token:
        store.mark_uncertain(
            principal,
            uncertain_id,
            claim_token="wrong-token",
            error=_delivery_error(),
        )
    assert wrong_token.value.code is ErrorCode.PERMISSION_DENIED
    store.mark_uncertain(
        principal,
        uncertain_id,
        claim_token=sending_uncertain.claim_token or "",
        error=_delivery_error(),
    )

    assert (
        store.claim_due(principal, NOW + timedelta(days=30), owner="future-worker")
        == []
    )
    rows = {message.id: message for message in store.list_for_tenant(principal)}
    assert rows[published_id].dispatch_status is DeliveryStatus.published
    assert rows[failed_id].dispatch_status is DeliveryStatus.failed
    assert rows[uncertain_id].dispatch_status is DeliveryStatus.uncertain
    assert {message.attempts for message in rows.values()} == {1}


def test_real_postgres_reschedule_clears_sending_boundary_until_next_attempt(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    principal = _principal("tenant-reschedule")
    store = PostgresOutbox(dsn=dsn, schema=schema)
    message_id = _add_message(store, principal, "reschedule")

    [claimed] = store.claim_due(principal, NOW, owner="worker-1")
    sending = store.mark_sending(
        principal,
        message_id,
        claim_token=claimed.claim_token or "",
        started_at=NOW + timedelta(seconds=1),
    )
    next_attempt_at = NOW + timedelta(hours=1)
    pending = store.reschedule(
        principal,
        message_id,
        claim_token=sending.claim_token or "",
        next_attempt_at=next_attempt_at,
        error=_delivery_error(),
    )

    assert pending.dispatch_status is DeliveryStatus.pending
    assert pending.claim_token is None
    assert pending.claim_owner is None
    assert pending.claimed_until is None
    assert pending.sending_at is None
    assert pending.attempts == 1
    assert (
        store.claim_due(
            principal, next_attempt_at - timedelta(microseconds=1), owner="worker-2"
        )
        == []
    )
    [reclaimed] = store.claim_due(principal, next_attempt_at, owner="worker-2")
    assert reclaimed.attempts == 1
    second_sending = store.mark_sending(
        principal,
        message_id,
        claim_token=reclaimed.claim_token or "",
        started_at=next_attempt_at,
    )
    assert second_sending.attempts == 2


def test_real_postgres_invalid_inputs_wrong_tokens_and_rollback_do_not_mutate(
    real_postgres_claims: tuple[str, str],
) -> None:
    dsn, schema = real_postgres_claims
    psycopg = pytest.importorskip("psycopg")
    principal = _principal("tenant-rollback")
    store = PostgresOutbox(dsn=dsn, schema=schema)
    message_id = _add_message(store, principal, "rollback")

    for arguments in (
        {"limit": 0, "owner": "worker", "lease_seconds": 30},
        {"limit": 1_001, "owner": "worker", "lease_seconds": 30},
        {"limit": True, "owner": "worker", "lease_seconds": 30},
        {"limit": 1, "owner": " ", "lease_seconds": 30},
        {"limit": 1, "owner": "w" * 201, "lease_seconds": 30},
        {"limit": 1, "owner": "worker\nother", "lease_seconds": 30},
        {"limit": 1, "owner": "worker\x00", "lease_seconds": 30},
        {"limit": 1, "owner": "wórker", "lease_seconds": 30},
        {"limit": 1, "owner": "worker", "lease_seconds": 0},
        {"limit": 1, "owner": "worker", "lease_seconds": 86_401},
        {"limit": 1, "owner": "worker", "lease_seconds": True},
    ):
        with pytest.raises(ValueError):
            store.claim_due(principal, NOW, **arguments)
    [unchanged] = store.list_for_tenant(principal)
    assert unchanged.id == message_id
    assert unchanged.dispatch_status is DeliveryStatus.pending
    assert unchanged.attempts == 0

    connection = psycopg.connect(dsn)
    try:
        transactional = PostgresOutbox(
            _database=_PostgresDatabase(
                connection=connection, schema=schema, commit=False
            )
        )
        [rolled_back] = transactional.claim_due(
            principal, NOW, owner="rollback-worker", lease_seconds=30
        )
        assert rolled_back.attempts == 0
        connection.rollback()
    finally:
        connection.close()

    [claimed] = store.claim_due(
        principal, NOW, owner="committed-worker", lease_seconds=30
    )
    assert claimed.attempts == 0
    with pytest.raises(AssistantError) as wrong_token:
        store.mark_sending(
            principal,
            message_id,
            claim_token="wrong-token",
            started_at=NOW + timedelta(seconds=1),
        )
    assert wrong_token.value.code is ErrorCode.PERMISSION_DENIED
    [still_claimed] = store.list_for_tenant(principal)
    assert still_claimed.dispatch_status is DeliveryStatus.claimed
    assert still_claimed.claim_token == claimed.claim_token
    assert still_claimed.attempts == 0
