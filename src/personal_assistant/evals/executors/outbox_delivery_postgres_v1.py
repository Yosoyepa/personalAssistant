"""Real PostgreSQL outbox delivery, lease, fencing, and concurrency evals."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.adapters.persistence.postgres import (
    PostgresOutbox,
    PostgresPersistence,
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
from personal_assistant.application.use_cases.reminder_notifications import (
    DispatchDueReminders,
)
from personal_assistant.domain.common.exceptions import AssistantError
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.evals.executors.postgres_reliability_support import (
    PostgresEvalDatabase,
    isolated_postgres,
    schema_exists,
)


NOW = datetime(2026, 7, 17, 15, tzinfo=UTC)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InputModel(_StrictModel):
    scenario: Literal[
        "claim-partition",
        "lease-reclaim",
        "transition",
        "stale-token",
        "sweep-sending",
        "dispatcher-outcome",
        "provider-crash",
        "resolution",
        "event-filter",
        "tenant-fence",
    ]
    operation: Literal[
        "claim",
        "publish",
        "fail",
        "uncertain",
        "reschedule",
        "release",
        "claim-failed",
        "success",
        "permanent",
        "transient",
        "unknown",
        "crash-before-response",
        "delivered",
        "retry",
        "filter",
        "fence",
    ]
    messageCount: int = Field(ge=1, le=24)
    workers: int = Field(ge=1, le=8)
    claimLimit: int = Field(ge=1, le=24)
    leaseSeconds: int = Field(ge=1, le=300)
    offsetSeconds: int = Field(ge=0, le=600)
    retryAfter: int | None = Field(default=None, ge=1, le=600)
    variant: int = Field(ge=1, le=30)
    providerCodeMode: Literal["present", "absent"] = "present"
    tenantProbe: Literal["transition", "claim"] = "transition"


class ExpectedModel(_StrictModel):
    scenario: str
    operation: str
    observation: str
    finalStatus: str
    claimedCount: int = Field(ge=0)
    pendingCount: int = Field(ge=0)
    attemptsTotal: int = Field(ge=0)
    providerCalls: int = Field(ge=0)
    concurrencyObservation: str
    fencingObservation: str
    scheduleDeltaSeconds: int | None
    schemaLifecycle: Literal["created-migrated-dropped"]


def _principal(tenant: str = "tenant-delivery") -> Principal:
    return Principal.for_test(
        principal_id=f"worker-{tenant}",
        tenant_id=tenant,
        permission_tier=PermissionTier.P5,
    )


def _event(index: int, tenant: str, *, event_type: str = "notification.requested") -> CloudEvent:
    return CloudEvent(
        id=f"event-{index}",
        type=event_type,
        source="eval",
        subject=f"reminder-{index}",
        tenant_id=tenant,
        time=NOW,
        data={"channel": "telegram", "recipient": "eval-chat", "body": f"body-{index}"},
    )


def _seed_outbox(
    store: PostgresOutbox,
    principal: Principal,
    count: int,
    *,
    event_type: str = "notification.requested",
    prefix: str = "delivery",
) -> list[OutboxMessage]:
    return [
        store.add(
            principal,
            _event(index, principal.tenant_id, event_type=event_type),
            idempotency_key=f"{prefix}-outbox-{index}",
            message_id=f"{prefix}-message-{index}",
            next_attempt_at=NOW,
        )
        for index in range(count)
    ]


def _seed_dispatch(
    persistence: PostgresPersistence, principal: Principal, suffix: str
) -> OutboxMessage:
    with persistence.reminder_uow.begin(principal) as transaction:
        job = transaction.scheduler.schedule_before_event(
            principal,
            calendar_event_id=f"calendar-{suffix}",
            starts_at=NOW,
            channel="telegram",
            recipient="eval-chat",
            body=f"body-{suffix}",
            timezone="America/Bogota",
            source_event_id=f"source-{suffix}",
            payload_fingerprint=(f"{int(suffix.split('-')[-1]):064x}"),
            minutes_before=0,
            idempotency_key=f"schedule-{suffix}",
            reminder_id=f"reminder-{suffix}",
        )
        message = transaction.outbox.add(
            principal,
            CloudEvent(
                id=f"event-{suffix}",
                type="notification.requested",
                source="eval",
                subject=job.reminder_id,
                tenant_id=principal.tenant_id,
                time=NOW,
                data={"channel": "telegram", "recipient": "eval-chat", "body": f"body-{suffix}"},
            ),
            idempotency_key=f"outbox-{suffix}",
            message_id=f"message-{suffix}",
            next_attempt_at=NOW,
        )
        transaction.commit()
    return message


def _error() -> DeliveryError:
    return DeliveryError(
        category=DeliveryErrorCategory.unknown,
        code=DeliveryErrorCode.unknown,
        occurred_at=NOW,
    )


def _summary(
    case: InputModel,
    messages: list[OutboxMessage],
    *,
    observation: str,
    provider_calls: int = 0,
    concurrency: str = "single-worker",
    fencing: str = "active-token-enforced",
    claimed_count: int | None = None,
    schedule_delta: int | None = None,
) -> dict[str, object]:
    statuses = {message.dispatch_status.value for message in messages}
    final_status = next(iter(statuses)) if len(statuses) == 1 else "mixed"
    return {
        "scenario": case.scenario,
        "operation": case.operation,
        "observation": observation,
        "finalStatus": final_status,
        "claimedCount": claimed_count if claimed_count is not None else sum(
            message.dispatch_status is DeliveryStatus.claimed for message in messages
        ),
        "pendingCount": sum(
            message.dispatch_status is DeliveryStatus.pending for message in messages
        ),
        "attemptsTotal": sum(message.attempts for message in messages),
        "providerCalls": provider_calls,
        "concurrencyObservation": concurrency,
        "fencingObservation": fencing,
        "scheduleDeltaSeconds": schedule_delta,
    }


def _claim_partition(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    seed = PostgresOutbox(dsn=db.dsn, schema=db.schema)
    _seed_outbox(seed, principal, case.messageCount)
    barrier = Barrier(case.workers)

    def claim(index: int) -> list[OutboxMessage]:
        local = PostgresOutbox(dsn=db.dsn, schema=db.schema)
        barrier.wait(timeout=10)
        return local.claim_due(
            principal,
            NOW,
            limit=case.claimLimit,
            owner=f"worker-{index}",
            lease_seconds=case.leaseSeconds,
        )

    with ThreadPoolExecutor(max_workers=case.workers) as pool:
        batches = list(pool.map(claim, range(case.workers)))
    ids = [message.id for batch in batches for message in batch]
    messages = seed.list_for_tenant(principal)
    observation = "skip-locked-disjoint" if len(ids) == len(set(ids)) else "duplicate-claim"
    return _summary(
        case,
        messages,
        observation=observation,
        concurrency="multiworker-disjoint-claims",
        claimed_count=len(ids),
    )


def _lease_reclaim(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    store = PostgresOutbox(dsn=db.dsn, schema=db.schema)
    [first] = _seed_outbox(store, principal, 1)
    [claimed] = store.claim_due(
        principal, NOW, owner="first", lease_seconds=case.leaseSeconds
    )
    reclaimed = store.claim_due(
        principal,
        NOW + timedelta(seconds=case.offsetSeconds),
        owner="second",
        lease_seconds=case.leaseSeconds,
    )
    current = store.list_for_tenant(principal)
    changed = bool(reclaimed and reclaimed[0].claim_token != claimed.claim_token)
    observation = "expired-lease-reclaimed" if changed else "live-lease-protected"
    return _summary(
        case,
        current,
        observation=observation,
        concurrency="restart-reclaim",
        claimed_count=len(reclaimed),
        fencing="new-token-issued" if changed else "original-token-retained",
    )


def _transition(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    store = PostgresOutbox(dsn=db.dsn, schema=db.schema)
    _seed_outbox(store, principal, 1)
    [claimed] = store.claim_due(principal, NOW, owner="worker", lease_seconds=60)
    token = claimed.claim_token or ""
    if case.operation == "release":
        store.release(principal, claimed.id, claim_token=token, next_attempt_at=NOW)
    elif case.operation == "claim-failed":
        store.mark_claim_failed(principal, claimed.id, claim_token=token, error=_error())
    else:
        store.mark_sending(principal, claimed.id, claim_token=token, started_at=NOW)
        if case.operation == "publish":
            store.mark_published(principal, claimed.id, claim_token=token, published_at=NOW)
        elif case.operation == "fail":
            store.mark_failed(principal, claimed.id, claim_token=token, error=_error())
        elif case.operation == "uncertain":
            store.mark_uncertain(principal, claimed.id, claim_token=token, error=_error())
        else:
            store.reschedule(
                principal,
                claimed.id,
                claim_token=token,
                next_attempt_at=NOW + timedelta(seconds=case.offsetSeconds),
                error=_error(),
            )
    return _summary(
        case,
        store.list_for_tenant(principal),
        observation="production-transition-committed",
        schedule_delta=case.offsetSeconds if case.operation == "reschedule" else None,
    )


def _stale_token(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    store = PostgresOutbox(dsn=db.dsn, schema=db.schema)
    _seed_outbox(store, principal, 1)
    [first] = store.claim_due(principal, NOW, owner="first", lease_seconds=1)
    [second] = store.claim_due(
        principal, NOW + timedelta(seconds=1), owner="second", lease_seconds=60
    )
    rejected = False
    try:
        if case.operation in {"publish", "fail", "uncertain", "reschedule"}:
            store.mark_sending(
                principal,
                second.id,
                claim_token=first.claim_token or "",
                started_at=NOW + timedelta(seconds=1),
            )
        elif case.operation == "release":
            store.release(principal, second.id, claim_token=first.claim_token or "")
        else:
            store.mark_claim_failed(
                principal, second.id, claim_token=first.claim_token or "", error=_error()
            )
    except AssistantError:
        rejected = True
    return _summary(
        case,
        store.list_for_tenant(principal),
        observation="stale-token-rejected" if rejected else "stale-token-accepted",
        fencing="stale-token-fenced",
    )


def _sweep(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    store = PostgresOutbox(dsn=db.dsn, schema=db.schema)
    _seed_outbox(store, principal, case.messageCount)
    claimed = store.claim_due(
        principal,
        NOW,
        limit=case.messageCount,
        owner="crashed-worker",
        lease_seconds=case.leaseSeconds,
    )
    for message in claimed:
        store.mark_sending(
            principal, message.id, claim_token=message.claim_token or "", started_at=NOW
        )
    swept = store.sweep_expired_sending(
        principal,
        NOW + timedelta(seconds=case.offsetSeconds),
        error=_error(),
        limit=case.claimLimit,
    )
    observation = "sending-swept-uncertain" if swept else "sending-lease-protected"
    return _summary(
        case,
        store.list_for_tenant(principal),
        observation=observation,
        claimed_count=len(swept),
        concurrency="crash-recovery-sweep",
    )


@dataclass(slots=True)
class _Provider:
    outcome: str
    provider_code: int | None = None
    retry_after: int | None = None
    crash: bool = False
    calls: int = 0

    def send(
        self,
        _principal_value: Principal,
        request: NotificationRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> NotificationResult:
        del approval
        self.calls += 1
        if self.crash:
            raise _InjectedProcessCrash("provider process stopped")
        success = self.outcome == "success"
        return NotificationResult(
            notification_id="telegram:101" if success else None,
            channel=request.channel,
            idempotency_key=request.idempotency_key,
            outcome=self.outcome,  # type: ignore[arg-type]
            provider_code=self.provider_code,
            retry_after=self.retry_after,
            provider_message_id=101 if success else None,
        )


class _InjectedProcessCrash(BaseException):
    pass


def _approval(
    principal: Principal, _message: OutboxMessage, dispatch_key: str
) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=principal,
        action="notification.send",
        resource=dispatch_key,
        tier=PermissionTier.P5,
    )


def _dispatcher(case: InputModel, db: PostgresEvalDatabase, *, crash: bool = False) -> dict[str, object]:
    principal = _principal()
    persistence = PostgresPersistence(dsn=db.dsn, schema=db.schema)
    _seed_dispatch(persistence, principal, f"dispatch-{case.variant}")
    mapping = {
        "success": ("success", 200),
        "permanent": ("permanent", 403),
        "transient": ("known-transient", 429),
        "unknown": ("unknown-outcome", 502),
        "crash-before-response": ("unknown-outcome", None),
    }
    outcome, code = mapping[case.operation]
    if case.operation == "success" and case.providerCodeMode == "absent":
        code = None
    provider = _Provider(
        outcome=outcome,
        provider_code=code,
        retry_after=case.retryAfter,
        crash=crash,
    )
    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=provider,
        owner="eval-worker",
        claim_limit=1,
        lease_seconds=case.leaseSeconds,
        clock=lambda: NOW,
    )
    crashed = False
    try:
        dispatcher.dispatch(principal, NOW, approval_provider=_approval)
    except _InjectedProcessCrash:
        crashed = True
    if crashed:
        recovery = DispatchDueReminders(
            unit_of_work=persistence.reminder_uow,
            notifications=_Provider(outcome="success"),
            owner="recovery-worker",
            claim_limit=1,
            lease_seconds=case.leaseSeconds,
            clock=lambda: NOW + timedelta(seconds=case.leaseSeconds),
        )
        recovery.dispatch(
            principal,
            NOW + timedelta(seconds=case.leaseSeconds),
            approval_provider=_approval,
        )
    messages = persistence.outbox.list_for_tenant(principal)
    delta = None
    if messages[0].next_attempt_at is not None:
        delta = int((messages[0].next_attempt_at - NOW).total_seconds())
    observation = (
        "crash-swept-without-resend"
        if crashed
        else (
            "invalid-success-metadata-recorded-uncertain"
            if case.operation == "success" and case.providerCodeMode == "present"
            else (
                "success-without-provider-code-durably-recorded"
                if case.operation == "success"
                else "provider-result-durably-recorded"
            )
        )
    )
    return _summary(
        case,
        messages,
        observation=observation,
        provider_calls=provider.calls,
        concurrency="provider-io-after-sending-commit",
        schedule_delta=delta,
    )


def _resolution(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    persistence = PostgresPersistence(dsn=db.dsn, schema=db.schema)
    seeded = _seed_dispatch(persistence, principal, f"resolution-{case.variant}")
    store = persistence.outbox
    [claimed] = store.claim_due(principal, NOW, owner="worker", lease_seconds=60)
    sending = store.mark_sending(
        principal, claimed.id, claim_token=claimed.claim_token or "", started_at=NOW
    )
    store.mark_uncertain(
        principal, sending.id, claim_token=sending.claim_token or "", error=_error()
    )
    dispatcher = DispatchDueReminders(
        unit_of_work=persistence.reminder_uow,
        notifications=_Provider(outcome="success"),
    )
    approval = ApprovalGrant.issue(
        principal=principal,
        action="notification.resolve_uncertain",
        resource=f"{seeded.id}:{case.operation}",
        tier=PermissionTier.P5,
    )
    dispatcher.resolve_uncertain(
        principal,
        seeded.id,
        resolution=case.operation,
        now=NOW + timedelta(seconds=case.offsetSeconds),
        approval=approval,
    )
    return _summary(
        case,
        store.list_for_tenant(principal),
        observation="p5-resolution-committed",
        schedule_delta=case.offsetSeconds if case.operation == "retry" else None,
    )


def _filter_or_fence(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    tenant_a = _principal("tenant-a")
    tenant_b = _principal("tenant-b")
    store = PostgresOutbox(dsn=db.dsn, schema=db.schema)
    if case.scenario == "event-filter":
        _seed_outbox(store, tenant_a, case.messageCount, event_type="audit.recorded")
        filtered_claims = store.claim_due(
            tenant_a,
            NOW,
            owner="worker",
            limit=case.claimLimit,
            event_type="notification.requested",
        )
        return _summary(
            case,
            store.list_for_tenant(tenant_a),
            observation="foreign-event-type-ignored",
            claimed_count=len(filtered_claims),
            fencing="event-type-filter-enforced",
        )
    _seed_outbox(store, tenant_a, 1)
    if case.tenantProbe == "claim":
        foreign_claims = store.claim_due(tenant_b, NOW, owner="foreign-worker")
        return _summary(
            case,
            store.list_for_tenant(tenant_a),
            observation=(
                "cross-tenant-claim-empty" if not foreign_claims else "tenant-leak"
            ),
            claimed_count=len(foreign_claims),
            fencing="tenant-boundary-enforced",
        )
    [tenant_claim] = store.claim_due(tenant_a, NOW, owner="worker")
    rejected = False
    try:
        store.mark_sending(
            tenant_b,
            tenant_claim.id,
            claim_token=tenant_claim.claim_token or "",
            started_at=NOW,
        )
    except AssistantError:
        rejected = True
    return _summary(
        case,
        store.list_for_tenant(tenant_a),
        observation="cross-tenant-transition-rejected" if rejected else "tenant-leak",
        fencing="tenant-boundary-enforced",
    )


def execute(case: InputModel) -> dict[str, object]:
    with isolated_postgres() as db:
        if case.scenario == "claim-partition":
            actual = _claim_partition(case, db)
        elif case.scenario == "lease-reclaim":
            actual = _lease_reclaim(case, db)
        elif case.scenario == "transition":
            actual = _transition(case, db)
        elif case.scenario == "stale-token":
            actual = _stale_token(case, db)
        elif case.scenario == "sweep-sending":
            actual = _sweep(case, db)
        elif case.scenario == "dispatcher-outcome":
            actual = _dispatcher(case, db)
        elif case.scenario == "provider-crash":
            actual = _dispatcher(case, db, crash=True)
        elif case.scenario == "resolution":
            actual = _resolution(case, db)
        else:
            actual = _filter_or_fence(case, db)
        dsn, schema = db.dsn, db.schema
    if schema_exists(dsn, schema):
        raise RuntimeError("isolated eval schema cleanup failed")
    actual["schemaLifecycle"] = "created-migrated-dropped"
    return actual
