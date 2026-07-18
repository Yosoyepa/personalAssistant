from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any

import pytest

from personal_assistant.application.dto.delivery import DeliveryStatus
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import build_runtime_container
from personal_assistant.infrastructure import worker as worker_module
from personal_assistant.infrastructure.worker import main as worker_main

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeClock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass(slots=True)
class FakeNotificationTool:
    results: list[NotificationResult | Exception]
    before_send: Any | None = None
    requests: list[NotificationRequest] = field(default_factory=list)

    def send(
        self,
        principal: Principal,
        request: NotificationRequest,
        *,
        approval: object | None = None,
    ) -> NotificationResult:
        if self.before_send is not None:
            self.before_send()
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        if result.idempotency_key == "placeholder":
            result = result.model_copy(
                update={"idempotency_key": request.idempotency_key}
            )
        return result


def principal() -> Principal:
    return Principal.for_test(
        principal_id="worker",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )


def success(key: str = "placeholder") -> NotificationResult:
    return NotificationResult(
        notification_id="telegram:101",
        channel="telegram",
        idempotency_key=key,
        provider_message_id=101,
    )


def transient(*, retry_after: int | None = None) -> NotificationResult:
    return NotificationResult(
        channel="telegram",
        idempotency_key="placeholder",
        outcome="known-transient",
        provider_code=503,
        retry_after=retry_after,
    )


def build_worker(
    provider: FakeNotificationTool, *, approve: bool = True
) -> tuple[Any, Principal, FakeClock]:
    actor = principal()
    clock = FakeClock()
    container = build_container(
        persistence_backend="memory",
        notifications=provider,
        approve_reminder_notifications=approve,
    )
    container.reminder_worker.approval_policy = (
        container.reminder_worker.approval_policy.__class__(
            approve_notifications=approve,
            approval_ttl=None,
        )
    )
    container.reminder_notifications.clock = clock
    container.reminder_worker.clock = clock
    return container, actor, clock


def add_due(
    container: Any,
    actor: Principal,
    *,
    key: str = "1",
    body: object = "Recordatorio",
) -> str:
    job = container.scheduler.schedule_before_event(
        actor,
        calendar_event_id=f"cal-{key}",
        starts_at=NOW,
        channel="telegram",
        recipient="chat-1",
        body="scheduler mirror only",
        minutes_before=0,
        idempotency_key=f"notify-{key}",
        timezone="America/Bogota",
        source_event_id=f"source-{key}",
        payload_fingerprint="a" * 64,
    )
    event = CloudEvent(
        type="notification.requested",
        source="test",
        subject=job.reminder_id,
        tenant_id=actor.tenant_id,
        data={"channel": "telegram", "recipient": "chat-1", "body": body},
    )
    return container.outbox.add(
        actor,
        event,
        idempotency_key=f"outbox-{key}",
        next_attempt_at=NOW,
    ).id


def message(container: Any, actor: Principal) -> Any:
    return container.outbox.list_for_tenant(actor)[0]


def mirror(container: Any, actor: Principal) -> Any:
    return container.scheduler.list_for_tenant(actor)[0]


def resolution_approval(
    actor: Principal, message_id: str, resolution: str
) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=actor,
        action="notification.resolve_uncertain",
        resource=f"{message_id}:{resolution}",
        tier=PermissionTier.P5,
    )


def test_worker_confirms_sending_in_both_stores_before_provider_io() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    message_id = add_due(container, actor)

    def assert_sending() -> None:
        assert message(container, actor).dispatch_status == DeliveryStatus.sending
        assert mirror(container, actor).delivery_status == DeliveryStatus.sending
        assert message(container, actor).attempts == 1

    provider.before_send = assert_sending
    tick = container.reminder_worker.run_once(actor, now=NOW)

    assert tick.sent_count == 1
    assert message(container, actor).dispatch_status == DeliveryStatus.published
    assert mirror(container, actor).delivery_status == DeliveryStatus.published
    assert provider.requests[0].idempotency_key == f"{message_id}:attempt:1"


def test_worker_ignores_generic_outbox_events_and_dispatches_notification() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    unrelated = container.outbox.add(
        actor,
        CloudEvent(
            type="audit.event.recorded",
            source="other-publisher",
            tenant_id=actor.tenant_id,
            data={"private": "must stay untouched"},
        ),
        idempotency_key="audit-outbox",
        next_attempt_at=NOW,
    )
    add_due(container, actor)

    tick = container.reminder_worker.run_once(actor, now=NOW)

    assert tick.sent_count == 1
    stored = next(
        item
        for item in container.outbox.list_for_tenant(actor)
        if item.id == unrelated.id
    )
    assert stored.dispatch_status == DeliveryStatus.pending
    assert stored.attempts == 0


def test_known_transient_uses_30s_2m_5m_then_fails_on_fourth_attempt() -> None:
    provider = FakeNotificationTool([transient() for _ in range(4)])
    container, actor, clock = build_worker(provider)
    add_due(container, actor)
    expected_delays = (30, 120, 300)

    for attempt, delay in enumerate(expected_delays, start=1):
        container.reminder_worker.run_once(actor, now=clock.now)
        current = message(container, actor)
        assert current.dispatch_status == DeliveryStatus.pending
        assert current.attempts == attempt
        assert current.next_attempt_at == clock.now + timedelta(seconds=delay)
        assert mirror(container, actor).attempts == attempt
        assert mirror(container, actor).next_attempt_at == current.next_attempt_at
        clock.now = current.next_attempt_at

    container.reminder_worker.run_once(actor, now=clock.now)
    assert message(container, actor).dispatch_status == DeliveryStatus.failed
    assert message(container, actor).attempts == 4
    assert len(provider.requests) == 4


def test_retry_after_wins_and_unrepresentable_value_is_safely_capped() -> None:
    provider = FakeNotificationTool([transient(retry_after=10**30)])
    container, actor, _clock = build_worker(provider)
    add_due(container, actor)

    container.reminder_worker.run_once(actor, now=NOW)

    assert message(container, actor).next_attempt_at == datetime.max.replace(tzinfo=UTC)


def test_unknown_exception_is_uncertain_and_requires_manual_resolution() -> None:
    provider = FakeNotificationTool([TimeoutError("private recipient and body")])
    container, actor, clock = build_worker(provider)
    message_id = add_due(container, actor)

    container.reminder_worker.run_once(actor, now=NOW)
    assert message(container, actor).dispatch_status == DeliveryStatus.uncertain
    assert container.reminder_notifications.list_uncertain(actor)[0].id == message_id

    with pytest.raises(AssistantError):
        container.reminder_notifications.resolve_uncertain(
            actor, message_id, resolution="retry", now=clock.now
        )
    low = Principal.for_test(
        principal_id=actor.principal_id,
        tenant_id=actor.tenant_id,
        permission_tier=PermissionTier.P3,
    )
    with pytest.raises(AssistantError):
        container.reminder_notifications.resolve_uncertain(
            low,
            message_id,
            resolution="retry",
            now=clock.now,
            approval=resolution_approval(low, message_id, "retry"),
        )

    retried = container.reminder_notifications.resolve_uncertain(
        actor,
        message_id,
        resolution="retry",
        now=clock.now,
        approval=resolution_approval(actor, message_id, "retry"),
    )
    assert retried.dispatch_status == DeliveryStatus.pending
    assert retried.attempts == 1


def test_mismatched_provider_result_is_uncertain_never_published() -> None:
    provider = FakeNotificationTool(
        [
            NotificationResult(
                notification_id="telegram:101",
                channel="telegram",
                idempotency_key="different-attempt",
                provider_message_id=101,
            )
        ]
    )
    container, actor, _clock = build_worker(provider)
    add_due(container, actor)

    container.reminder_worker.run_once(actor, now=NOW)

    assert message(container, actor).dispatch_status == DeliveryStatus.uncertain


def test_missing_scheduler_after_sending_does_not_rollback_outbox_terminal() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    add_due(container, actor)
    original_mirror = container.scheduler.mirror_delivery
    provider_started = False

    def mark_provider_started() -> None:
        nonlocal provider_started
        provider_started = True

    def disappearing_mirror(
        principal: Principal, reminder_id: str, outbox_message: Any
    ) -> Any:
        if provider_started:
            raise AssistantError(
                ErrorCode.NOT_FOUND,
                "scheduled reminder not found",
                tenant_id=principal.tenant_id,
            )
        return original_mirror(principal, reminder_id, outbox_message)

    provider.before_send = mark_provider_started
    container.scheduler.mirror_delivery = disappearing_mirror

    first = container.reminder_worker.run_once(actor, now=NOW)
    second = container.reminder_worker.run_once(actor, now=NOW)

    assert first.sent_count == 1
    assert second.due_count == 0
    assert message(container, actor).dispatch_status == DeliveryStatus.published
    assert len(provider.requests) == 1


def test_manual_retry_is_rejected_after_four_attempts_but_delivered_is_allowed() -> (
    None
):
    provider = FakeNotificationTool(
        [transient(), transient(), transient(), TimeoutError("ambiguous")]
    )
    container, actor, clock = build_worker(provider)
    message_id = add_due(container, actor)
    for _ in range(4):
        container.reminder_worker.run_once(actor, now=clock.now)
        current = message(container, actor)
        if current.next_attempt_at is not None:
            clock.now = current.next_attempt_at
    assert message(container, actor).dispatch_status == DeliveryStatus.uncertain

    with pytest.raises(ValueError, match="maximum delivery attempts"):
        container.reminder_notifications.resolve_uncertain(
            actor,
            message_id,
            resolution="retry",
            now=clock.now,
            approval=resolution_approval(actor, message_id, "retry"),
        )
    delivered = container.reminder_notifications.resolve_uncertain(
        actor,
        message_id,
        resolution="delivered",
        now=clock.now,
        approval=resolution_approval(actor, message_id, "delivered"),
    )
    assert delivered.dispatch_status == DeliveryStatus.published


def test_expired_claim_is_reclaimed_but_expired_sending_is_only_swept() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, clock = build_worker(provider)
    add_due(container, actor)
    [claimed] = container.outbox.claim_due(actor, NOW, lease_seconds=10)
    clock.now = NOW + timedelta(seconds=10)

    container.reminder_worker.run_once(actor, now=clock.now)
    assert message(container, actor).dispatch_status == DeliveryStatus.published

    provider2 = FakeNotificationTool([success()])
    container2, actor2, clock2 = build_worker(provider2)
    add_due(container2, actor2)
    [claimed2] = container2.outbox.claim_due(actor2, NOW, lease_seconds=10)
    container2.outbox.mark_sending(
        actor2, claimed2.id, claim_token=claimed2.claim_token or "", started_at=NOW
    )
    clock2.now = NOW + timedelta(seconds=10)

    tick = container2.reminder_worker.run_once(actor2, now=clock2.now)
    assert tick.swept_message_ids == (claimed2.id,)
    assert message(container2, actor2).dispatch_status == DeliveryStatus.uncertain
    assert provider2.requests == []


def test_malformed_payload_fails_once_without_provider_io() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    add_due(container, actor, body=None)

    container.reminder_worker.run_once(actor, now=NOW)

    assert message(container, actor).dispatch_status == DeliveryStatus.failed
    assert message(container, actor).attempts == 0
    assert message(container, actor).sending_at is None
    assert mirror(container, actor).attempts == 0
    assert mirror(container, actor).sending_at is None
    assert provider.requests == []


def test_missing_scheduler_subject_fails_pre_io_without_looping() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    corrupt = container.outbox.add(
        actor,
        CloudEvent(
            type="notification.requested",
            source="test",
            tenant_id=actor.tenant_id,
            data={
                "channel": "telegram",
                "recipient": "chat-1",
                "body": "body",
            },
        ),
        idempotency_key="corrupt-no-subject",
        next_attempt_at=NOW,
    )

    first = container.reminder_worker.run_once(actor, now=NOW)
    second = container.reminder_worker.run_once(actor, now=NOW)

    stored = next(
        item
        for item in container.outbox.list_for_tenant(actor)
        if item.id == corrupt.id
    )
    assert first.skipped_reminder_ids == (corrupt.id,)
    assert second.due_count == 0
    assert stored.dispatch_status == DeliveryStatus.failed
    assert stored.attempts == 0
    assert provider.requests == []


def test_missing_approval_releases_before_io_without_incrementing_attempts() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider, approve=False)
    add_due(container, actor)

    container.reminder_worker.run_once(actor, now=NOW)

    assert message(container, actor).dispatch_status == DeliveryStatus.pending
    assert message(container, actor).attempts == 0
    assert provider.requests == []


def test_p3_principal_cannot_dispatch_and_other_tenant_is_not_claimed() -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    other = Principal.for_test(
        principal_id="other",
        tenant_id="tenant-b",
        permission_tier=PermissionTier.P5,
    )
    low = Principal.for_test(
        principal_id="low",
        tenant_id="tenant-low",
        permission_tier=PermissionTier.P3,
    )
    add_due(container, actor, key="tenant-a")
    add_due(container, other, key="tenant-b")
    add_due(container, low, key="tenant-low")

    container.reminder_worker.run_once(actor, now=NOW)
    assert len(provider.requests) == 1
    assert (
        container.outbox.list_for_tenant(other)[0].dispatch_status
        == DeliveryStatus.pending
    )

    container.reminder_worker.run_once(low, now=NOW)
    assert (
        container.outbox.list_for_tenant(low)[0].dispatch_status
        == DeliveryStatus.pending
    )
    assert container.outbox.list_for_tenant(low)[0].attempts == 0
    assert len(provider.requests) == 1


def test_run_loop_is_bounded_and_one_failure_does_not_block_next_message() -> None:
    provider = FakeNotificationTool([TimeoutError("private"), success()])
    container, actor, _clock = build_worker(provider)
    add_due(container, actor, key="first")
    add_due(container, actor, key="second")
    sleeps: list[float] = []
    container.reminder_worker.sleep = sleeps.append

    ticks = container.reminder_worker.run_loop(
        actor, interval_seconds=0.25, max_ticks=2
    )

    assert len(ticks) == 2
    assert ticks[0].uncertain_message_ids
    assert ticks[1].sent_count == 1
    assert sleeps == [0.25]
    assert len(provider.requests) == 2


def test_runtime_worker_rejects_memory_persistence() -> None:
    settings = AppSettings(reminder_worker_enabled=True, persistence_backend="memory")
    with pytest.raises(RuntimeError, match="requires PERSISTENCE_BACKEND=postgres"):
        build_runtime_container(settings)


def test_dispatcher_owner_is_unique_safe_and_non_identifying() -> None:
    first, _actor, _clock = build_worker(FakeNotificationTool([success()]))
    second, _actor2, _clock2 = build_worker(FakeNotificationTool([success()]))
    owners = {
        first.reminder_notifications.owner,
        second.reminder_notifications.owner,
    }
    assert len(owners) == 2
    assert all(re.fullmatch(r"reminder-worker:[0-9a-f]{32}", owner) for owner in owners)


def test_runtime_worker_rejects_missing_telegram_token() -> None:
    settings = AppSettings(
        reminder_worker_enabled=True,
        persistence_backend="postgres",
        database_url="postgresql://not-opened",
        telegram_bot_token=None,
    )
    with pytest.raises(RuntimeError, match="requires TELEGRAM_BOT_TOKEN"):
        build_runtime_container(settings)


def test_cli_requires_exact_confirmation_without_echoing_sensitive_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = worker_main(
        [
            "resolve-uncertain",
            "--message-id",
            "out_safe",
            "--resolution",
            "retry",
            "--confirm",
            "wrong-secret-body",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "confirmation_mismatch" in output
    assert "wrong-secret-body" not in output


def test_cli_run_once_outputs_only_ids_claimed_by_that_tick(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = FakeNotificationTool([success()])
    container, actor, _clock = build_worker(provider)
    claimed_id = add_due(container, actor, key="claimed")
    unrelated = container.outbox.add(
        actor,
        CloudEvent(
            type="notification.requested",
            source="test",
            subject="not-claimed",
            tenant_id=actor.tenant_id,
            data={
                "channel": "telegram",
                "recipient": "private-recipient",
                "body": "private-body",
            },
        ),
        idempotency_key="future-unrelated",
        next_attempt_at=NOW + timedelta(days=30),
    )
    monkeypatch.setattr(
        worker_module,
        "_runtime",
        lambda *, require_provider: (container, actor, None),
    )

    assert worker_main(["run-once"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in rows] == [claimed_id]
    assert unrelated.id not in str(rows)
    assert "private-recipient" not in str(rows)
    assert "private-body" not in str(rows)


def test_cli_exact_confirmation_resolves_uncertain(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = FakeNotificationTool([TimeoutError("private")])
    container, actor, _clock = build_worker(provider)
    message_id = add_due(container, actor)
    container.reminder_worker.run_once(actor, now=NOW)
    monkeypatch.setattr(
        worker_module,
        "_runtime",
        lambda *, require_provider: (container, actor, None),
    )

    exit_code = worker_main(
        [
            "resolve-uncertain",
            "--message-id",
            message_id,
            "--resolution",
            "delivered",
            "--confirm",
            message_id,
        ]
    )
    rows = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert len(rows) == 1
    assert rows[0]["message_id"] == message_id
    assert rows[0]["status"] == "published"
    assert rows[0]["attempts"] == 1
