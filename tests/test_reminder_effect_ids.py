from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
)
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage
from personal_assistant.application.ports.calendar import (
    CalendarEventRequest,
    CalendarEventResult,
)
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.reminders.idempotency import (
    ReminderEffectIds,
    ReminderIdempotency,
    ReminderIdempotencyIdentity,
    ReminderPayload,
    reminder_effect_ids,
)


NOW = datetime(2026, 7, 17, 18, tzinfo=UTC)
STARTS_AT = datetime(2026, 7, 18, 18, tzinfo=UTC)


def _identity(**overrides: str) -> ReminderIdempotencyIdentity:
    values = {
        "tenant_id": "tenant-a",
        "channel": "telegram",
        "principal_id": "User-1",
        "conversation_id": "chat-1",
        "source_event_id": "event-42",
    }
    values.update(overrides)
    return ReminderIdempotencyIdentity(**values)


def _principal(tenant_id: str = "tenant-a") -> Principal:
    return Principal.for_test(
        principal_id="User-1",
        tenant_id=tenant_id,
        permission_tier=PermissionTier.P5,
    )


def _calendar_request(
    ids: ReminderEffectIds,
    key: str,
    *,
    title: str = "Cita estable",
) -> CalendarEventRequest:
    return CalendarEventRequest(
        event_id=ids.calendar_event_id,
        title=title,
        starts_at=STARTS_AT,
        timezone="UTC",
        idempotency_key=f"{key}:calendar",
        source_event_id="event-42",
        payload_fingerprint="a" * 64,
    )


def _approval(principal: Principal, request: CalendarEventRequest) -> ApprovalGrant:
    return ApprovalGrant.issue(
        principal=principal,
        action="calendar.create_event",
        resource=request.idempotency_key,
        tier=PermissionTier.P3,
    )


def _reminder_created_event(
    ids: ReminderEffectIds, key: str, tenant_id: str = "tenant-a"
) -> CloudEvent:
    return CloudEvent(
        id=ids.reminder_created_event_id,
        type="reminder.created",
        source="test.reminders",
        subject=ids.reminder_id,
        tenant_id=tenant_id,
        correlation_id=key,
        causation_id="event-42",
        source_event_id="event-42",
        payload_fingerprint="a" * 64,
        timezone="UTC",
        data={"calendar_event_id": ids.calendar_event_id},
        time=NOW,
    )


def _notification_requested_event(
    ids: ReminderEffectIds, key: str, tenant_id: str = "tenant-a"
) -> CloudEvent:
    return CloudEvent(
        id=ids.notification_requested_event_id,
        type="notification.requested",
        source="test.reminders",
        subject=ids.reminder_id,
        tenant_id=tenant_id,
        correlation_id=key,
        causation_id=ids.reminder_created_event_id,
        source_event_id="event-42",
        payload_fingerprint="a" * 64,
        timezone="UTC",
        data={"reminder_id": ids.reminder_id},
        time=NOW,
    )


def test_effect_ids_are_domain_separated_full_sha256_known_vectors() -> None:
    ids = reminder_effect_ids(_identity())

    assert ids.model_dump() == {
        "calendar_event_id": (
            "cal_v2_c3addee38273b9e53a3df66a0ec887b5c51d44fd7188d485fa50c8d77bf8801f"
        ),
        "reminder_id": (
            "rem_v2_d44010eb30a8eb3b8a1a49f29605fefe1268e68de6c883553c31db6bb6c3b3bc"
        ),
        "reminder_created_event_id": (
            "evt_reminder_created_v2_d3362fb89130cce5df484eeaa62c775f"
            "f80fe5f5447b7dbd97715347b77fc6da"
        ),
        "notification_requested_event_id": (
            "evt_notification_requested_v2_344b728af7ebce0a0010ebfd3b755d01"
            "0066c014c010f84f39357dad252ef622"
        ),
        "outbox_message_id": (
            "out_v2_ca393581a82ca62e2680129e2299e5e72f1856c94cd7ff1cf194609644c1a9bd"
        ),
    }
    prefixes = {
        "calendar_event_id": "cal_v2_",
        "reminder_id": "rem_v2_",
        "reminder_created_event_id": "evt_reminder_created_v2_",
        "notification_requested_event_id": "evt_notification_requested_v2_",
        "outbox_message_id": "out_v2_",
    }
    values = ids.model_dump()
    for field, prefix in prefixes.items():
        assert re.fullmatch(r"[0-9a-f]{64}", values[field].removeprefix(prefix))
    assert len(set(values.values())) == len(values)
    assert ids.reminder_created_event_id != ids.notification_requested_event_id


def test_effect_ids_are_stable_across_processes_and_hash_seeds() -> None:
    script = """
import json
from personal_assistant.domain.reminders.idempotency import (
    ReminderIdempotencyIdentity,
    reminder_effect_ids,
)
identity = ReminderIdempotencyIdentity(
    tenant_id="tenant-a",
    channel="telegram",
    principal_id="User-1",
    conversation_id="chat-1",
    source_event_id="event-42",
)
print(json.dumps(reminder_effect_ids(identity).model_dump(), sort_keys=True))
"""
    outputs: list[dict[str, str]] = []
    for seed in ("1", "987654"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
        )
        outputs.append(json.loads(completed.stdout))

    assert outputs[0] == outputs[1] == reminder_effect_ids(_identity()).model_dump()


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("tenant_id", "tenant-b"),
        ("channel", "whatsapp"),
        ("principal_id", "User-2"),
        ("conversation_id", "chat-2"),
        ("source_event_id", "event-43"),
    ],
)
def test_every_identity_dimension_changes_every_effect_id(
    field: str, changed: str
) -> None:
    original = reminder_effect_ids(_identity()).model_dump()
    altered = reminder_effect_ids(_identity(**{field: changed})).model_dump()

    assert all(original[name] != altered[name] for name in original)


def test_payload_changes_do_not_change_ids_but_keep_a_distinct_fingerprint() -> None:
    identity = _identity()
    first = ReminderIdempotency(
        identity=identity,
        payload=ReminderPayload(text="pagar", recipient="chat-1", timezone="UTC"),
    )
    changed = ReminderIdempotency(
        identity=identity,
        payload=ReminderPayload(text="cobrar", recipient="chat-1", timezone="UTC"),
    )

    assert first.effect_ids == changed.effect_ids
    assert first.payload_fingerprint != changed.payload_fingerprint


def test_all_effect_stores_replay_stable_ids_under_concurrency() -> None:
    identity = _identity()
    key = identity.idempotency_key
    ids = identity_ids = reminder_effect_ids(identity)
    principal = _principal()
    calendar = LocalCalendarTool()
    scheduler = ReminderScheduler()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    calendar_request = _calendar_request(ids, key)
    approval = _approval(principal, calendar_request)
    reminder_event = _reminder_created_event(ids, key)
    notification_event = _notification_requested_event(ids, key)

    def write_all(_: int) -> tuple[str, str, str, str, str]:
        calendar_result = calendar.create_event(
            principal, calendar_request, approval=approval
        )
        scheduled = scheduler.schedule_before_event(
            principal,
            calendar_event_id=calendar_result.event_id,
            starts_at=STARTS_AT,
            channel="telegram",
            recipient="chat-1",
            body="Cita estable",
            timezone="UTC",
            source_event_id="event-42",
            payload_fingerprint="a" * 64,
            idempotency_key=f"{key}:notify",
            reminder_id=identity_ids.reminder_id,
        )
        stored_event = event_store.append(principal, reminder_event)
        message = outbox.add(
            principal,
            notification_event,
            idempotency_key=f"{key}:outbox",
            message_id=identity_ids.outbox_message_id,
        )
        return (
            calendar_result.event_id,
            scheduled.reminder_id,
            stored_event.id,
            message.event.id,
            message.id,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(write_all, range(64)))

    assert set(results) == {
        (
            ids.calendar_event_id,
            ids.reminder_id,
            ids.reminder_created_event_id,
            ids.notification_requested_event_id,
            ids.outbox_message_id,
        )
    }
    assert len(calendar.list_events(principal)) == 1
    assert len(scheduler.list_for_tenant(principal)) == 1
    assert len(event_store.list_for_tenant(principal)) == 1
    assert len(outbox.list_for_tenant(principal)) == 1


def test_explicit_id_collisions_and_changed_payloads_remain_conflicts() -> None:
    identity = _identity()
    key = identity.idempotency_key
    ids = reminder_effect_ids(identity)
    principal = _principal()
    calendar = LocalCalendarTool()
    scheduler = ReminderScheduler()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()

    request = _calendar_request(ids, key)
    calendar.create_event(principal, request, approval=_approval(principal, request))
    changed_request = _calendar_request(ids, key, title="Payload distinto")
    with pytest.raises(AssistantError) as calendar_payload_error:
        calendar.create_event(
            principal,
            changed_request,
            approval=_approval(principal, changed_request),
        )
    assert calendar_payload_error.value.code == ErrorCode.CONFLICT

    colliding_request = request.model_copy(
        update={"idempotency_key": f"{key}:other-calendar"}
    )
    with pytest.raises(AssistantError) as calendar_id_error:
        calendar.create_event(
            principal,
            colliding_request,
            approval=_approval(principal, colliding_request),
        )
    assert calendar_id_error.value.code == ErrorCode.CONFLICT

    scheduler_kwargs = {
        "calendar_event_id": ids.calendar_event_id,
        "starts_at": STARTS_AT,
        "channel": "telegram",
        "recipient": "chat-1",
        "body": "Cita estable",
        "timezone": "UTC",
        "source_event_id": "event-42",
        "payload_fingerprint": "a" * 64,
        "idempotency_key": f"{key}:notify",
        "reminder_id": ids.reminder_id,
    }
    scheduler.schedule_before_event(principal, **scheduler_kwargs)
    with pytest.raises(AssistantError) as reminder_id_error:
        scheduler.schedule_before_event(
            principal,
            **{**scheduler_kwargs, "idempotency_key": f"{key}:other-notify"},
        )
    assert reminder_id_error.value.code == ErrorCode.CONFLICT

    event = _reminder_created_event(ids, key)
    event_store.append(principal, event)
    with pytest.raises(AssistantError) as event_payload_error:
        event_store.append(
            principal,
            event.model_copy(update={"data": {"calendar_event_id": "changed"}}),
        )
    assert event_payload_error.value.code == ErrorCode.CONFLICT

    notification = _notification_requested_event(ids, key)
    outbox.add(
        principal,
        notification,
        idempotency_key=f"{key}:outbox",
        message_id=ids.outbox_message_id,
    )
    with pytest.raises(AssistantError) as outbox_payload_error:
        outbox.add(
            principal,
            notification.model_copy(update={"data": {"reminder_id": "changed"}}),
            idempotency_key=f"{key}:outbox",
            message_id=ids.outbox_message_id,
        )
    assert outbox_payload_error.value.code == ErrorCode.CONFLICT

    other_event = notification.model_copy(update={"id": f"{notification.id}:other"})
    with pytest.raises(AssistantError) as outbox_id_error:
        outbox.add(
            principal,
            other_event,
            idempotency_key=f"{key}:other-outbox",
            message_id=ids.outbox_message_id,
        )
    assert outbox_id_error.value.code == ErrorCode.CONFLICT


def test_v1_ids_remain_readable_and_new_optional_ids_remain_backward_compatible() -> (
    None
):
    legacy_event = CloudEvent(
        id="123e4567-e89b-12d3-a456-426614174000",
        type="reminder.created",
        source="legacy",
        tenant_id="tenant-a",
        time=NOW,
    )
    legacy_calendar = CalendarEventResult(
        event_id="cal_0123456789abcdef0123456789abcdef",
        title="Legacy",
        starts_at=STARTS_AT,
        timezone="UTC",
        idempotency_key="reminder:v1:legacy:calendar",
    )
    legacy_reminder = ScheduledReminder(
        reminder_id="rem_0123456789abcdef0123456789abcdef",
        tenant_id="tenant-a",
        calendar_event_id=legacy_calendar.event_id,
        notify_at=STARTS_AT,
        timezone="UTC",
        source_event_id="legacy-event",
        payload_fingerprint="f" * 64,
        channel="telegram",
        recipient="chat-1",
        body="Legacy",
        idempotency_key="reminder:v1:legacy:notify",
    )
    legacy_outbox = OutboxMessage(
        id="out_0123456789abcdef0123456789abcdef",
        tenant_id="tenant-a",
        event=legacy_event,
        idempotency_key="reminder:v1:legacy:outbox",
        created_at=NOW,
    )

    assert CloudEvent.model_validate(legacy_event.model_dump()).id == legacy_event.id
    assert (
        CalendarEventResult.model_validate(legacy_calendar.model_dump()).event_id
        == legacy_calendar.event_id
    )
    assert (
        ScheduledReminder.model_validate(legacy_reminder.model_dump()).reminder_id
        == legacy_reminder.reminder_id
    )
    assert (
        OutboxMessage.model_validate(legacy_outbox.model_dump()).id == legacy_outbox.id
    )
