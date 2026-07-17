from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from personal_assistant.adapters.persistence.in_memory import InMemoryWorkflowStateStore
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.use_cases.reminders import reminder_idempotency
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.reminders.idempotency import (
    REMINDER_IDEMPOTENCY_KEY_PREFIX,
    ReminderIdempotency,
    ReminderIdempotencyConflict,
    ReminderIdempotencyIdentity,
    ReminderPayload,
)


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


def _state(
    *,
    tenant_id: str = "tenant-a",
    workflow_id: str = "wf-1",
    idempotency_key: str | None = None,
    payload_fingerprint: str = "a" * 64,
) -> WorkflowState:
    return WorkflowState(
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        workflow_type="reminder.create",
        status=WorkflowStatus.running,
        step="classify",
        idempotency_key=idempotency_key
        or f"{REMINDER_IDEMPOTENCY_KEY_PREFIX}{'b' * 64}",
        payload_fingerprint=payload_fingerprint,
    )


def _principal(*, tenant_id: str = "tenant-a") -> Principal:
    return Principal.for_test(principal_id="User-1", tenant_id=tenant_id)


def test_identity_key_is_deterministic_versioned_and_uses_full_sha256() -> None:
    identity = _identity()

    assert identity.idempotency_key == (
        "reminder:v2:42d7bba3c4fac2edb83edb6cf5f83eac2fde3172c9e4f5b0ce4a39e0cbcf7266"
    )
    assert identity.idempotency_key == _identity().idempotency_key
    assert identity.idempotency_key.startswith(REMINDER_IDEMPOTENCY_KEY_PREFIX)
    assert (
        len(identity.idempotency_key.removeprefix(REMINDER_IDEMPOTENCY_KEY_PREFIX))
        == 64
    )
    assert (
        '"schema":"personal-assistant.reminder-idempotency-identity"'
        in identity.canonical_json()
    )
    assert identity.canonical_document()["version"] == 2


def test_canonical_json_has_unambiguous_field_boundaries() -> None:
    left = _identity(tenant_id="a:b", channel="c")
    right = _identity(tenant_id="a", channel="b:c")

    assert ":".join(
        left.canonical_document()[field] for field in ("tenant_id", "channel")
    ) == ":".join(
        right.canonical_document()[field] for field in ("tenant_id", "channel")
    )
    assert left.canonical_json() != right.canonical_json()
    assert left.idempotency_key != right.idempotency_key


def test_normalization_is_explicit_and_opaque_ids_remain_case_sensitive() -> None:
    normalized = _identity(
        tenant_id=" tenant-a ",
        channel=" TELEGRAM ",
        principal_id=" Cafe\u0301 ",
        conversation_id=" chat-1 ",
        source_event_id=" event-42 ",
    )
    equivalent = _identity(principal_id="Caf\u00e9")

    assert normalized == equivalent
    assert normalized.idempotency_key == equivalent.idempotency_key
    assert (
        _identity(principal_id="User-1").idempotency_key
        != _identity(principal_id="user-1").idempotency_key
    )


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
def test_each_identity_dimension_prevents_collision(field: str, changed: str) -> None:
    assert _identity().idempotency_key != _identity(**{field: changed}).idempotency_key


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "channel", "principal_id", "conversation_id", "source_event_id"],
)
def test_blank_identity_dimensions_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _identity(**{field: " \t "})


def test_payload_fingerprint_is_separate_full_sha256() -> None:
    first = ReminderPayload(
        text=" pagar arriendo ", recipient=" chat-1 ", timezone=" UTC "
    )
    equivalent = ReminderPayload(
        text="pagar arriendo", recipient="chat-1", timezone="UTC"
    )
    changed = ReminderPayload(
        text="pagar servicios", recipient="chat-1", timezone="UTC"
    )

    assert first.fingerprint == equivalent.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert len(first.fingerprint) == 64
    assert "pagar arriendo" not in first.fingerprint
    first_claim = ReminderIdempotency(identity=_identity(), payload=first)
    changed_claim = ReminderIdempotency(identity=_identity(), payload=changed)
    assert first_claim.key == changed_claim.key
    assert first_claim.payload_fingerprint != changed_claim.payload_fingerprint


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("text", "pagar servicios"),
        ("recipient", "chat-2"),
        ("timezone", "America/Bogota"),
    ],
)
def test_payload_dimensions_change_fingerprint_but_not_identity_key(
    field: str,
    changed: str,
) -> None:
    identity = _identity()
    payload = ReminderPayload(text="pagar arriendo", recipient="chat-1", timezone="UTC")
    changed_payload = payload.model_copy(update={field: changed})

    original = ReminderIdempotency(identity=identity, payload=payload)
    altered = ReminderIdempotency(identity=identity, payload=changed_payload)

    assert altered.key == original.key
    assert altered.payload_fingerprint != original.payload_fingerprint


@pytest.mark.parametrize("control_field", ["now", "approval", "idempotency_key"])
def test_replay_control_fields_do_not_change_identity_or_payload_fingerprint(
    control_field: str,
) -> None:
    principal = _principal()
    request = ReminderWorkflowInput(
        message_id="message-42",
        source_event_id="event-42",
        conversation_id="chat-1",
        text="pagar arriendo",
        channel="telegram",
        recipient="chat-1",
        now=datetime(2026, 7, 17, 12, tzinfo=UTC),
        timezone="UTC",
    )
    original = reminder_idempotency(principal, request)
    changed_value: object
    if control_field == "now":
        changed_value = request.now + timedelta(days=1)
    elif control_field == "approval":
        changed_value = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource=f"{original.key}:calendar",
            tier=PermissionTier.P3,
        )
    else:
        changed_value = "caller-supplied-assertion"

    changed = reminder_idempotency(
        principal,
        request.model_copy(update={control_field: changed_value}),
    )

    assert changed.identity == original.identity
    assert changed.key == original.key
    assert changed.payload_fingerprint == original.payload_fingerprint


def test_register_or_replay_reuses_same_payload_without_overwrite() -> None:
    principal = _principal()
    store = InMemoryWorkflowStateStore()
    first = _state(workflow_id="wf-first")
    duplicate = _state(workflow_id="wf-duplicate")

    registered = store.register_or_replay(principal, first)
    replayed = store.register_or_replay(principal, duplicate)

    assert registered.replayed is False
    assert replayed.replayed is True
    assert replayed.state.workflow_id == "wf-first"
    assert (
        store.get_by_idempotency_key(principal, first.idempotency_key)
        == registered.state
    )


def test_changed_payload_raises_typed_conflict_without_sensitive_metadata() -> None:
    principal = _principal()
    store = InMemoryWorkflowStateStore()
    original = _state(payload_fingerprint="a" * 64)
    store.register_or_replay(principal, original)

    with pytest.raises(ReminderIdempotencyConflict) as captured:
        store.register_or_replay(principal, _state(payload_fingerprint="c" * 64))

    error = captured.value.model_dump()
    assert captured.value.code == ErrorCode.CONFLICT
    assert error["error"]["context"] == {
        "idempotency_key": original.idempotency_key,
        "identity_version": 2,
    }
    assert "a" * 64 not in str(error)
    assert "c" * 64 not in str(error)
    persisted = store.get_by_idempotency_key(principal, original.idempotency_key)
    assert persisted is not None
    assert persisted.payload_fingerprint == "a" * 64


def test_store_remains_tenant_scoped_even_for_same_raw_key() -> None:
    store = InMemoryWorkflowStateStore()
    key = f"{REMINDER_IDEMPOTENCY_KEY_PREFIX}{'d' * 64}"
    tenant_a = _principal(tenant_id="tenant-a")
    tenant_b = _principal(tenant_id="tenant-b")

    first = store.register_or_replay(
        tenant_a, _state(tenant_id="tenant-a", idempotency_key=key)
    )
    second = store.register_or_replay(
        tenant_b,
        _state(tenant_id="tenant-b", workflow_id="wf-2", idempotency_key=key),
    )

    assert first.replayed is False
    assert second.replayed is False
    assert store.get_by_idempotency_key(tenant_a, key).tenant_id == "tenant-a"  # type: ignore[union-attr]
    assert store.get_by_idempotency_key(tenant_b, key).tenant_id == "tenant-b"  # type: ignore[union-attr]


def test_register_or_replay_is_atomic_under_concurrency() -> None:
    principal = _principal()
    store = InMemoryWorkflowStateStore()

    def register(index: int):
        return store.register_or_replay(principal, _state(workflow_id=f"wf-{index}"))

    with ThreadPoolExecutor(max_workers=16) as pool:
        registrations = list(pool.map(register, range(64)))

    assert sum(not item.replayed for item in registrations) == 1
    assert len({item.state.workflow_id for item in registrations}) == 1
    assert len(store.list_for_tenant(principal)) == 1


def test_waiting_resume_atomically_elects_one_running_executor() -> None:
    principal = _principal()
    store = InMemoryWorkflowStateStore()
    original = _state(workflow_id="wf-waiting")
    store.register_or_replay(principal, original)
    waiting = original.transition(
        status=WorkflowStatus.waiting_approval, step="approval_required"
    )
    store.upsert(principal, waiting)

    def resume(index: int):
        return store.register_or_replay(
            principal,
            _state(workflow_id=f"wf-resume-{index}"),
            resume_from_step="approval_required",
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        registrations = list(pool.map(resume, range(32)))

    assert sum(item.resumed for item in registrations) == 1
    assert sum(not item.replayed for item in registrations) == 1
    assert all(item.state.status == WorkflowStatus.running for item in registrations)
    persisted = store.get_by_idempotency_key(principal, original.idempotency_key)
    assert persisted is not None
    assert persisted.status == WorkflowStatus.running
    assert persisted.workflow_id == original.workflow_id


def test_upsert_cannot_change_or_remove_registered_identity_or_fingerprint() -> None:
    principal = _principal()
    store = InMemoryWorkflowStateStore()
    original = _state()
    store.register_or_replay(principal, original)

    with pytest.raises(ReminderIdempotencyConflict):
        store.upsert(
            principal, original.model_copy(update={"payload_fingerprint": None})
        )

    with pytest.raises(AssistantError, match="identity is immutable"):
        store.upsert(
            principal,
            original.model_copy(
                update={
                    "idempotency_key": f"{REMINDER_IDEMPOTENCY_KEY_PREFIX}{'e' * 64}"
                }
            ),
        )

    with pytest.raises(AssistantError, match="identity is immutable"):
        store.upsert(
            principal, original.model_copy(update={"workflow_type": "different"})
        )
