"""Focused regressions for phase-one validation and persistence contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryApprovalStore,
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.application.dto.commands import PendingApproval
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.dto.reminders import ReminderWorkflowResult
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.calendar import (
    CalendarEventRequest,
    CalendarEventResult,
)
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.reminders.models import (
    ReminderClarificationReason,
    ReminderIntent,
)


NOW = datetime(2026, 7, 17, 15, tzinfo=UTC)


def _principal(*, tenant_id: str = "tenant-a") -> Principal:
    return Principal.for_test(
        principal_id="user-1",
        tenant_id=tenant_id,
        permission_tier=PermissionTier.P5,
    )


def _pending_approval(**overrides: object) -> PendingApproval:
    values: dict[str, object] = {
        "approval_id": "apr-1",
        "tenant_id": "tenant-a",
        "principal_id": "user-1",
        "action": "calendar.create_event",
        "resource": "calendar-key",
        "tier": PermissionTier.P3.value,
        "workflow_kind": "reminder.create",
        "message_id": "message-1",
        "source_event_id": "event-1",
        "conversation_id": "chat-1",
        "channel": "telegram",
        "recipient": "chat-1",
        "request_text": "recuérdame mañana a las 17 pagar",
        "request_now": NOW,
        "timezone": "America/Bogota",
        "idempotency_key": "reminder-key",
        "payload_fingerprint": "a" * 64,
        "created_at": NOW,
    }
    values.update(overrides)
    return PendingApproval.model_validate(values)


def _workflow(**overrides: object) -> WorkflowState:
    values: dict[str, object] = {
        "workflow_id": "wf-1",
        "tenant_id": "tenant-a",
        "workflow_type": "reminder.create",
        "status": WorkflowStatus.running,
        "step": "classify",
        "idempotency_key": f"reminder:v2:{'b' * 64}",
        "payload_fingerprint": "c" * 64,
    }
    values.update(overrides)
    return WorkflowState.model_validate(values)


def _workflow_result(**overrides: object) -> ReminderWorkflowResult:
    values: dict[str, object] = {
        "status": AgentStatus.completed,
        "intent": ReminderIntent.create,
        "reply": "Recordatorio creado",
        "idempotency_key": f"reminder:v2:{'d' * 64}",
        "source_event_id": "event-1",
        "payload_fingerprint": "e" * 64,
        "timezone": "America/Bogota",
    }
    values.update(overrides)
    return ReminderWorkflowResult.model_validate(values)


def test_pending_approval_rejects_invalid_timezone_and_naive_reference_time() -> None:
    with pytest.raises(ValidationError) as captured:
        _pending_approval(
            timezone="Mars/Olympus",
            request_now=datetime(2026, 7, 17, 10),
        )

    messages = {error["msg"] for error in captured.value.errors()}
    assert "Value error, timezone must be a valid IANA timezone" in messages
    assert "Value error, request_now must be timezone-aware" in messages


def test_cloud_event_rejects_invalid_timezone_and_naive_event_time() -> None:
    with pytest.raises(ValidationError) as captured:
        CloudEvent(
            type="reminder.created",
            source="test",
            tenant_id="tenant-a",
            timezone="Mars/Olympus",
            time=datetime(2026, 7, 17, 10),
        )

    messages = {error["msg"] for error in captured.value.errors()}
    assert "Value error, timezone must be a valid IANA timezone" in messages
    assert "Value error, event time must be timezone-aware" in messages


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param(
            {"timezone": "Mars/Olympus"},
            "timezone must be valid unless requesting timezone clarification",
            id="invalid-effect-timezone",
        ),
        pytest.param(
            {"clarification_reply_id": "reminder_missing_time"},
            "clarification reply identity requires a reason",
            id="identity-without-reason",
        ),
        pytest.param(
            {
                "clarification_reason": ReminderClarificationReason.missing_time,
                "clarification_reply_id": "reminder_missing_date",
                "clarification_reply_version": "v1",
            },
            "clarification reply id must match its typed reason",
            id="mismatched-identity",
        ),
        pytest.param(
            {
                "clarification_reason": ReminderClarificationReason.missing_time,
                "clarification_reply_id": "reminder_missing_time",
            },
            "clarification reply version is required",
            id="missing-version",
        ),
    ],
)
def test_reminder_result_enforces_timezone_and_clarification_identity(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _workflow_result(**overrides)


def test_calendar_contract_rejects_invalid_zones_and_naive_instants() -> None:
    with pytest.raises(ValidationError) as request_error:
        CalendarEventRequest(
            title="Cita",
            starts_at=datetime(2026, 7, 17, 10),
            ends_at=None,
            timezone="Mars/Olympus",
            idempotency_key="calendar-key",
        )
    assert len(request_error.value.errors()) == 2

    with pytest.raises(ValidationError) as result_error:
        CalendarEventResult(
            event_id="calendar-1",
            title="Cita",
            starts_at=datetime(2026, 7, 17, 10),
            timezone="Mars/Olympus",
            idempotency_key="calendar-key",
        )
    assert len(result_error.value.errors()) == 2


def test_scheduler_dto_rejects_invalid_zone_and_naive_notification_time() -> None:
    with pytest.raises(ValidationError) as captured:
        ScheduledReminder(
            tenant_id="tenant-a",
            calendar_event_id="calendar-1",
            notify_at=datetime(2026, 7, 17, 10),
            timezone="Mars/Olympus",
            source_event_id="event-1",
            payload_fingerprint="f" * 64,
            channel="telegram",
            recipient="chat-1",
            body="Recordatorio",
            idempotency_key="reminder-key",
        )

    assert len(captured.value.errors()) == 2


def test_local_scheduler_rejects_conflicting_replay_and_unknown_job() -> None:
    principal = _principal()
    scheduler = ReminderScheduler()
    arguments = {
        "calendar_event_id": "calendar-1",
        "starts_at": NOW,
        "channel": "telegram",
        "recipient": "chat-1",
        "body": "Recordatorio",
        "timezone": "America/Bogota",
        "source_event_id": "event-1",
        "payload_fingerprint": "a" * 64,
        "idempotency_key": "scheduler-key",
    }
    scheduler.schedule_before_event(principal, **arguments)

    with pytest.raises(AssistantError) as conflict:
        scheduler.schedule_before_event(
            principal,
            **{**arguments, "body": "Contenido diferente"},
        )
    assert conflict.value.code == ErrorCode.CONFLICT

    with pytest.raises(AssistantError) as missing:
        scheduler.mark_sent(principal, "missing-reminder")
    assert missing.value.code == ErrorCode.NOT_FOUND


def test_in_memory_event_and_outbox_enforce_tenant_boundary() -> None:
    principal = _principal()
    foreign_event = CloudEvent(
        type="reminder.created",
        source="test",
        tenant_id="tenant-b",
        time=NOW,
    )

    with pytest.raises(AssistantError) as event_error:
        InMemoryEventStore().append(principal, foreign_event)
    assert event_error.value.code == ErrorCode.PERMISSION_DENIED

    with pytest.raises(AssistantError) as outbox_error:
        InMemoryOutbox().add(principal, foreign_event, idempotency_key="outbox-key")
    assert outbox_error.value.code == ErrorCode.PERMISSION_DENIED


def test_in_memory_outbox_rejects_missing_messages_and_invalid_claims() -> None:
    principal = _principal()
    outbox = InMemoryOutbox()

    with pytest.raises(AssistantError) as missing_publish:
        outbox.mark_published(principal, "missing-message", claim_token="missing")
    assert missing_publish.value.code == ErrorCode.NOT_FOUND

    event = CloudEvent(
        type="reminder.created",
        source="test",
        tenant_id=principal.tenant_id,
        time=NOW,
    )
    outbox.add(principal, event, idempotency_key="outbox-key")
    [claimed] = outbox.claim(principal)

    with pytest.raises(AssistantError) as invalid_claim:
        outbox.release(principal, claimed.id, claim_token="wrong-token")
    assert invalid_claim.value.code == ErrorCode.PERMISSION_DENIED

    with pytest.raises(AssistantError) as missing_release:
        outbox.release(principal, "missing-message", claim_token="missing")
    assert missing_release.value.code == ErrorCode.NOT_FOUND


def test_in_memory_workflow_registration_rejects_invalid_identity_boundaries() -> None:
    principal = _principal()
    store = InMemoryWorkflowStateStore()

    with pytest.raises(ValueError, match="payload_fingerprint is required"):
        store.register_or_replay(
            principal,
            _workflow(payload_fingerprint=None),
        )

    with pytest.raises(AssistantError) as tenant_error:
        store.register_or_replay(principal, _workflow(tenant_id="tenant-b"))
    assert tenant_error.value.code == ErrorCode.PERMISSION_DENIED

    original = _workflow()
    store.register_or_replay(principal, original)

    with pytest.raises(AssistantError) as type_conflict:
        store.register_or_replay(
            principal,
            original.model_copy(update={"workflow_type": "different"}),
        )
    assert type_conflict.value.code == ErrorCode.CONFLICT

    with pytest.raises(AssistantError) as key_conflict:
        store.register_or_replay(
            principal,
            original.model_copy(update={"idempotency_key": "different-key"}),
        )
    assert key_conflict.value.code == ErrorCode.CONFLICT


def test_in_memory_approval_store_rejects_identity_and_terminal_conflicts() -> None:
    principal = _principal()
    store = InMemoryApprovalStore()

    with pytest.raises(AssistantError) as identity_error:
        store.create(principal, _pending_approval(tenant_id="tenant-b"))
    assert identity_error.value.code == ErrorCode.PERMISSION_DENIED

    for operation in (store.mark_approved, store.approve, store.cancel):
        with pytest.raises(AssistantError) as missing:
            operation(principal, "missing-approval")
        assert missing.value.code == ErrorCode.NOT_FOUND

    first = store.create(principal, _pending_approval())
    store.mark_approved(principal, first.approval_id)
    with pytest.raises(AssistantError) as approved_conflict:
        store.cancel(principal, first.approval_id)
    assert approved_conflict.value.code == ErrorCode.CONFLICT

    second = store.create(
        principal,
        _pending_approval(approval_id="apr-2", idempotency_key="reminder-key-2"),
    )
    cancelled = store.cancel(principal, second.approval_id)
    assert store.cancel(principal, second.approval_id) == cancelled
