from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from personal_assistant.adapters.persistence.in_memory import InMemoryOutbox
from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
    DeliveryStatus,
)
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def principal(tenant_id: str = "tenant-a") -> Principal:
    return Principal.for_test(
        principal_id=f"user-{tenant_id}",
        tenant_id=tenant_id,
        permission_tier=PermissionTier.P2,
    )


def add_message(outbox: InMemoryOutbox, actor: Principal) -> str:
    event = CloudEvent(
        type="notification.requested",
        source="test",
        tenant_id=actor.tenant_id,
    )
    return outbox.add(actor, event, idempotency_key="delivery-1").id


def sanitized_error() -> DeliveryError:
    return DeliveryError(
        category=DeliveryErrorCategory.network,
        code=DeliveryErrorCode.timeout,
        provider_code=408,
        occurred_at=NOW + timedelta(seconds=2),
    )


def test_outbox_requires_sending_before_terminal_and_replay_is_rejected() -> None:
    outbox = InMemoryOutbox()
    actor = principal()
    message_id = add_message(outbox, actor)
    [claimed] = outbox.claim_due(actor, NOW, owner="worker-a")
    token = claimed.claim_token or ""

    with pytest.raises(AssistantError) as illegal:
        outbox.mark_published(
            actor,
            message_id,
            claim_token=token,
            published_at=NOW + timedelta(seconds=1),
        )
    assert illegal.value.code == ErrorCode.CONFLICT

    sending = outbox.mark_sending(
        actor,
        message_id,
        claim_token=token,
        started_at=NOW + timedelta(seconds=1),
    )
    assert sending.dispatch_status == DeliveryStatus.sending
    assert sending.claimed_until == NOW + timedelta(seconds=60)
    assert outbox.claim_due(actor, NOW + timedelta(hours=1)) == []

    published = outbox.mark_published(
        actor,
        message_id,
        claim_token=token,
        published_at=NOW + timedelta(seconds=2),
    )
    assert published.claim_token is None
    with pytest.raises(AssistantError) as replay:
        outbox.mark_published(
            actor,
            message_id,
            claim_token=token,
            published_at=NOW + timedelta(days=1),
        )
    assert replay.value.code == ErrorCode.PERMISSION_DENIED
    with pytest.raises(AssistantError) as terminal:
        outbox.release(actor, message_id, claim_token=token)
    assert terminal.value.code == ErrorCode.PERMISSION_DENIED


def test_only_expired_claim_is_reclaimable_and_attempts_are_deterministic() -> None:
    outbox = InMemoryOutbox()
    actor = principal()
    add_message(outbox, actor)

    [first] = outbox.claim_due(actor, NOW, owner="worker-a", lease_seconds=10)
    assert first.attempts == 0
    assert outbox.claim_due(actor, NOW + timedelta(seconds=9)) == []

    [second] = outbox.claim_due(
        actor,
        NOW + timedelta(seconds=10),
        owner="worker-b",
        lease_seconds=20,
    )
    assert second.attempts == 0
    assert second.claimed_until == NOW + timedelta(seconds=30)


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"limit": True}, TypeError),
        ({"limit": 0}, ValueError),
        ({"limit": 1001}, ValueError),
        ({"lease_seconds": False}, TypeError),
        ({"lease_seconds": 0}, ValueError),
        ({"lease_seconds": 86_401}, ValueError),
        ({"owner": " "}, ValueError),
        ({"owner": "x" * 201}, ValueError),
        ({"owner": "worker name"}, ValueError),
        ({"owner": "worker\nadmin"}, ValueError),
        ({"owner": "wörker"}, ValueError),
        ({"owner": 7}, TypeError),
    ],
)
def test_claim_bounds_are_validated_before_mutation(
    kwargs: dict[str, object], error_type: type[Exception]
) -> None:
    outbox = InMemoryOutbox()
    actor = principal()
    add_message(outbox, actor)

    with pytest.raises(error_type):
        outbox.claim_due(actor, NOW, **kwargs)  # type: ignore[arg-type]

    assert outbox.list_for_tenant(actor)[0].attempts == 0


def test_claim_owner_is_normalized_before_persistence() -> None:
    outbox = InMemoryOutbox()
    actor = principal()
    add_message(outbox, actor)

    [claimed] = outbox.claim_due(actor, NOW, owner="  worker-a  ")

    assert claimed.claim_owner == "worker-a"


def test_uncertain_never_returns_to_pending_and_token_is_tenant_scoped() -> None:
    outbox = InMemoryOutbox()
    actor = principal()
    other = principal("tenant-b")
    message_id = add_message(outbox, actor)
    [claimed] = outbox.claim_due(actor, NOW)
    token = claimed.claim_token or ""
    outbox.mark_sending(
        actor,
        message_id,
        claim_token=token,
        started_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(AssistantError) as hidden:
        outbox.mark_uncertain(
            other,
            message_id,
            claim_token=token,
            error=sanitized_error(),
        )
    assert hidden.value.code == ErrorCode.NOT_FOUND

    uncertain = outbox.mark_uncertain(
        actor,
        message_id,
        claim_token=token,
        error=sanitized_error(),
    )
    assert uncertain.dispatch_status == DeliveryStatus.uncertain
    assert uncertain.claim_token is None
    assert uncertain.claimed_until is None
    assert outbox.claim_due(actor, NOW + timedelta(days=1)) == []
    with pytest.raises(AssistantError) as no_release:
        outbox.release(actor, message_id, claim_token=token)
    assert no_release.value.code == ErrorCode.PERMISSION_DENIED


def test_transient_known_failure_reschedules_at_exact_caller_time() -> None:
    outbox = InMemoryOutbox()
    actor = principal()
    message_id = add_message(outbox, actor)
    [claimed] = outbox.claim_due(actor, NOW)
    token = claimed.claim_token or ""
    outbox.mark_sending(
        actor,
        message_id,
        claim_token=token,
        started_at=NOW + timedelta(seconds=1),
    )
    retry_at = NOW + timedelta(minutes=5)

    pending = outbox.reschedule(
        actor,
        message_id,
        claim_token=token,
        next_attempt_at=retry_at,
        error=sanitized_error(),
    )

    assert pending.dispatch_status == DeliveryStatus.pending
    assert pending.next_attempt_at == retry_at
    assert pending.sending_at is None
    assert pending.attempts == 1
    assert outbox.claim_due(actor, retry_at - timedelta(microseconds=1)) == []
    [retried] = outbox.claim_due(actor, retry_at)
    assert retried.attempts == 1
    second_sending = outbox.mark_sending(
        actor,
        message_id,
        claim_token=retried.claim_token or "",
        started_at=retry_at,
    )
    assert second_sending.attempts == 2


def test_error_metadata_rejects_free_text_and_extra_sensitive_fields() -> None:
    with pytest.raises(ValidationError):
        DeliveryError.model_validate(
            {
                "category": "network",
                "code": "https://provider.example/token/secret",
                "occurred_at": NOW,
            }
        )
    with pytest.raises(ValidationError):
        DeliveryError.model_validate(
            {
                "category": "network",
                "code": "timeout",
                "occurred_at": NOW,
                "message": "recipient +57 and body",
            }
        )


@pytest.mark.parametrize("status", [DeliveryStatus.uncertain])
def test_terminal_error_states_require_attempt_and_delivery_evidence(
    status: DeliveryStatus,
) -> None:
    event = CloudEvent(
        type="notification.requested",
        source="test",
        tenant_id="tenant-a",
    )

    with pytest.raises(ValidationError):
        OutboxMessage(
            tenant_id="tenant-a",
            event=event,
            idempotency_key="invalid-terminal",
            dispatch_status=status,
        )


def test_pre_io_failed_state_requires_sanitized_error_without_attempt() -> None:
    event = CloudEvent(
        type="notification.requested",
        source="test",
        tenant_id="tenant-a",
    )
    failed = OutboxMessage(
        tenant_id="tenant-a",
        event=event,
        idempotency_key="invalid-payload",
        dispatch_status=DeliveryStatus.failed,
        attempts=0,
        sending_at=None,
        last_error=sanitized_error(),
    )
    assert failed.attempts == 0
    assert failed.sending_at is None


@pytest.mark.parametrize(
    ("attempts", "sending_at"),
    [(0, NOW), (1, None)],
)
def test_failed_state_rejects_mismatched_io_evidence(
    attempts: int, sending_at: datetime | None
) -> None:
    event = CloudEvent(
        type="notification.requested",
        source="test",
        tenant_id="tenant-a",
    )
    with pytest.raises(ValidationError):
        OutboxMessage(
            tenant_id="tenant-a",
            event=event,
            idempotency_key="invalid-failed-evidence",
            dispatch_status=DeliveryStatus.failed,
            attempts=attempts,
            sending_at=sending_at,
            last_error=sanitized_error(),
        )


def test_scheduler_upgrades_legacy_sent_payload_and_keeps_rollback_field() -> None:
    base = {
        "reminder_id": "rem-legacy",
        "tenant_id": "tenant-a",
        "calendar_event_id": "cal-1",
        "notify_at": NOW,
        "timezone": "America/Bogota",
        "source_event_id": "source-1",
        "payload_fingerprint": "a" * 64,
        "channel": "telegram",
        "recipient": "recipient",
        "body": "body",
        "idempotency_key": "idem-1",
    }

    legacy = ScheduledReminder.model_validate({**base, "sent": True})
    assert legacy.delivery_status == DeliveryStatus.published
    assert legacy.sent is True

    typed = ScheduledReminder.model_validate(
        {**base, "delivery_status": DeliveryStatus.uncertain, "sent": True}
    )
    assert typed.delivery_status == DeliveryStatus.uncertain
    assert typed.sent is True
    assert "sent" in typed.model_dump(mode="json")


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.claimed,
        DeliveryStatus.sending,
        DeliveryStatus.published,
        DeliveryStatus.failed,
        DeliveryStatus.uncertain,
    ],
)
def test_scheduler_sent_is_rollback_guard_for_every_non_pending_status(
    status: DeliveryStatus,
) -> None:
    reminder = ScheduledReminder.model_validate(
        {
            "reminder_id": f"rem-{status.value}",
            "tenant_id": "tenant-a",
            "calendar_event_id": "cal-1",
            "notify_at": NOW,
            "timezone": "America/Bogota",
            "source_event_id": "source-1",
            "payload_fingerprint": "a" * 64,
            "channel": "telegram",
            "recipient": "recipient",
            "body": "body",
            "idempotency_key": f"idem-{status.value}",
            "delivery_status": status,
            "sent": False,
        }
    )

    assert reminder.sent is True
