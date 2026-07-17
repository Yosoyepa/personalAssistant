"""Adversarial reminder boundaries at the Telegram webhook adapter."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from personal_assistant.adapters.inbound.api import normalize_telegram_webhook
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer, build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import create_app


WEBHOOK_PATH_SEGMENT = "test_webhook_path_segment"


class _NoNetworkNotificationProvider:
    """A provider fake that fails if a webhook test attempts network egress."""

    def send(self, principal, request, *, approval=None):  # type: ignore[no-untyped-def]
        raise AssertionError("Telegram boundary tests must not send notifications")


def _container() -> AppContainer:
    return build_container(
        llm=None,
        notifications=_NoNetworkNotificationProvider(),
    )


def _client(
    container: AppContainer,
    *,
    timezone: str = "America/Bogota",
    tenant_id: str = "tenant-a",
) -> TestClient:
    return TestClient(
        create_app(
            container,
            settings=AppSettings(
                tenant_id=tenant_id,
                timezone=timezone,
                telegram_webhook_secret=WEBHOOK_PATH_SEGMENT,
                telegram_allowed_user_ids=frozenset({"456", "789"}),
                reminder_worker_enabled=False,
            ),
        )
    )


def _payload(
    *,
    update_id: int = 900,
    message_id: int = 42,
    actor_id: str = "456",
    conversation_id: str = "chat-1",
    text: str = "/recordar recuérdame mañana a las 17 cerrar caja",
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": conversation_id},
            "from": {"id": actor_id},
            "text": text,
        },
    }


def _principal(*, tenant_id: str = "tenant-a", principal_id: str = "456") -> Principal:
    return Principal.for_test(
        tenant_id=tenant_id,
        principal_id=principal_id,
        permission_tier=PermissionTier.P5,
    )


def _effect_counts(container: AppContainer, principal: Principal) -> tuple[int, ...]:
    return (
        len(container.calendar.list_events(principal)),
        len(container.scheduler.list_for_tenant(principal)),
        len(container.event_store.list_for_tenant(principal)),
        len(container.outbox.list_for_tenant(principal)),
    )


def _post_at(
    client: TestClient,
    payload: dict[str, object],
    *,
    now: datetime,
):
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    with patch("personal_assistant.infrastructure.http.datetime", _FrozenDateTime):
        return client.post(
            f"/webhooks/telegram/{WEBHOOK_PATH_SEGMENT}",
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_PATH_SEGMENT},
            json=payload,
        )


def test_normalizer_uses_update_id_as_source_and_keeps_message_reference() -> None:
    normalized = normalize_telegram_webhook(
        _payload(update_id=777, message_id=42),
        tenant_id="tenant-a",
    )

    assert normalized.source_event_id == "777"
    assert normalized.message_id == "42"
    assert normalized.source_event_id != normalized.message_id
    assert normalized.idempotency_key == "telegram:777"


def test_telegram_midnight_survives_pending_approval_with_update_identity() -> None:
    container = _container()
    client = _client(container, timezone="America/Bogota")
    first_payload = _payload(
        update_id=800,
        message_id=42,
        text="/recordar recuérdame mañana a la medianoche cerrar caja",
    )
    expected_key = reminder_idempotency_key(
        tenant_id="tenant-a",
        channel="telegram",
        principal_id="456",
        conversation_id="chat-1",
        source_event_id="800",
    )
    pending = _post_at(
        client,
        first_payload,
        now=datetime(2026, 12, 31, 4, 59, tzinfo=UTC),
    )

    assert pending.status_code == 200, pending.text
    assert pending.json()["status"] == AgentStatus.escalated.value
    approval_id = pending.json()["approval_id"]
    assert approval_id is not None
    stored = container.approvals.get(_principal(), approval_id)
    assert stored is not None
    assert stored.source_event_id == "800"
    assert stored.message_id == "42"
    assert stored.idempotency_key == expected_key

    approved = _post_at(
        _client(container, timezone="UTC"),
        _payload(
            update_id=801,
            message_id=43,
            text=f"/aprobar {approval_id}",
        ),
        now=datetime(2026, 12, 31, 5, 1, tzinfo=UTC),
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == AgentStatus.completed.value
    state = container.states.get_by_idempotency_key(_principal(), expected_key)
    assert state is not None
    assert state.status.value == "completed"
    [event] = container.calendar.list_events(_principal())
    assert event.starts_at == datetime(2026, 12, 31, 5, 0, tzinfo=UTC)
    assert _effect_counts(container, _principal()) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("timezone", "now", "text"),
    [
        pytest.param(
            "America/New_York",
            datetime(2026, 3, 7, 12, tzinfo=UTC),
            "/recordar recuérdame mañana a las 2:30 am revisar la alarma",
            id="dst-gap",
        ),
        pytest.param(
            "America/New_York",
            datetime(2026, 10, 31, 12, tzinfo=UTC),
            "/recordar recuérdame mañana a las 1:30 am revisar la alarma",
            id="dst-fold",
        ),
        pytest.param(
            "America/Bogota",
            datetime(2026, 6, 20, 23, tzinfo=UTC),
            "/recordar recuérdame hoy a las 17 cerrar caja",
            id="elapsed-today",
        ),
    ],
)
def test_telegram_temporal_rejection_has_no_approval_or_effects(
    timezone: str, now: datetime, text: str
) -> None:
    container = _container()
    response = _post_at(
        _client(container, timezone=timezone),
        _payload(text=text),
        now=now,
    )
    principal = _principal()

    assert response.status_code == 200, response.text
    assert response.json()["status"] == AgentStatus.needs_clarification.value
    assert container.approvals.list_for_tenant(principal) == []
    assert _effect_counts(container, principal) == (0, 0, 0, 0)


def test_same_update_replays_once_and_changed_text_is_acknowledged_without_effects() -> (
    None
):
    container = _container()
    client = _client(container)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)
    original = _payload(update_id=901, message_id=51)

    first = _post_at(client, original, now=now)
    replay = _post_at(client, original, now=now)

    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == replay.json()["status"] == "escalated"
    assert first.json()["approval_id"] == replay.json()["approval_id"]
    assert len(container.approvals.list_for_tenant(_principal())) == 1
    assert _effect_counts(container, _principal()) == (0, 0, 0, 0)

    conflict = _post_at(
        client,
        _payload(
            update_id=901,
            message_id=51,
            text="/recordar recuérdame mañana a las 17 cerrar la oficina",
        ),
        now=now,
    )

    assert conflict.status_code == 200, conflict.text
    assert conflict.json()["status"] == AgentStatus.failed.value
    assert conflict.json()["sent"] is False
    assert conflict.json()["audio_sent"] is False
    assert conflict.json()["approval_id"] is None
    assert "reminder:v2:" not in conflict.text
    assert "idempotency_key" not in conflict.text
    assert "payload_fingerprint" not in conflict.text
    assert len(container.approvals.list_for_tenant(_principal())) == 1
    assert len(container.states.list_for_tenant(_principal())) == 1
    assert _effect_counts(container, _principal()) == (0, 0, 0, 0)


def test_same_message_reference_with_distinct_updates_creates_distinct_events() -> None:
    container = _container()
    client = _client(container)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    first = _post_at(client, _payload(update_id=910, message_id=52), now=now)
    second = _post_at(client, _payload(update_id=911, message_id=52), now=now)

    assert first.status_code == second.status_code == 200
    assert first.json()["approval_id"] != second.json()["approval_id"]
    approvals = container.approvals.list_for_tenant(_principal())
    assert {approval.source_event_id for approval in approvals} == {"910", "911"}
    assert {approval.message_id for approval in approvals} == {"52"}
    assert len({approval.idempotency_key for approval in approvals}) == 2
    assert _effect_counts(container, _principal()) == (0, 0, 0, 0)


def test_changed_runtime_timezone_is_acknowledged_without_effects_or_metadata() -> None:
    container = _container()
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)
    payload = _payload(update_id=920, message_id=53)

    first = _post_at(_client(container, timezone="America/Bogota"), payload, now=now)
    conflict = _post_at(_client(container, timezone="UTC"), payload, now=now)

    assert first.status_code == 200, first.text
    assert first.json()["status"] == AgentStatus.escalated.value
    assert conflict.status_code == 200, conflict.text
    assert conflict.json()["status"] == AgentStatus.failed.value
    assert conflict.json()["sent"] is False
    assert conflict.json()["audio_sent"] is False
    assert conflict.json()["approval_id"] is None
    assert "reminder:v2:" not in conflict.text
    assert "idempotency_key" not in conflict.text
    assert "payload_fingerprint" not in conflict.text
    assert len(container.approvals.list_for_tenant(_principal())) == 1
    assert len(container.states.list_for_tenant(_principal())) == 1
    assert _effect_counts(container, _principal()) == (0, 0, 0, 0)


def test_webhook_principal_and_conversation_dimensions_do_not_collide() -> None:
    container = _container()
    client = _client(container)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)
    variants = [
        _payload(
            update_id=930, message_id=54, actor_id="456", conversation_id="chat-1"
        ),
        _payload(
            update_id=930, message_id=54, actor_id="789", conversation_id="chat-1"
        ),
        _payload(
            update_id=930, message_id=54, actor_id="456", conversation_id="chat-2"
        ),
    ]

    responses = [_post_at(client, payload, now=now) for payload in variants]

    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["approval_id"] for response in responses}) == 3
    user_456 = container.approvals.list_for_tenant(_principal(principal_id="456"))
    user_789 = container.approvals.list_for_tenant(_principal(principal_id="789"))
    assert len(user_456) == 2
    assert len(user_789) == 1
    assert len({approval.idempotency_key for approval in [*user_456, *user_789]}) == 3
    assert _effect_counts(container, _principal(principal_id="456")) == (0, 0, 0, 0)
    assert _effect_counts(container, _principal(principal_id="789")) == (0, 0, 0, 0)
