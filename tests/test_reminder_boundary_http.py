"""Adversarial reminder boundaries at the local HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer, build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import create_app


BASE_HEADERS = {
    "X-Principal-Id": "user-1",
    "X-Tenant-Id": "tenant-a",
    "X-Permission-Tier": "P5",
}


class _NoNetworkNotificationProvider:
    """A provider fake that fails if an HTTP test attempts outbound delivery."""

    def send(self, principal, request, *, approval=None):  # type: ignore[no-untyped-def]
        raise AssertionError("HTTP boundary tests must not send notifications")


def _container() -> AppContainer:
    return build_container(
        llm=None,
        notifications=_NoNetworkNotificationProvider(),
    )


def _client(container: AppContainer, *, tenant_id: str = "tenant-a") -> TestClient:
    return TestClient(
        create_app(
            container,
            settings=AppSettings(
                tenant_id=tenant_id,
                reminder_worker_enabled=False,
            ),
        )
    )


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "message_id": "provider-message-42",
        "source_event_id": "http-event-900",
        "conversation_id": "chat-1",
        "text": "recuérdame mañana a las 17 cerrar caja",
        "channel": "telegram",
        "recipient": "chat-1",
        "now": "2026-06-20T12:00:00+00:00",
        "timezone": "America/Bogota",
    }
    payload.update(overrides)
    return payload


def _principal(headers: dict[str, str] | None = None) -> Principal:
    selected = headers or BASE_HEADERS
    return Principal.for_test(
        principal_id=selected["X-Principal-Id"],
        tenant_id=selected["X-Tenant-Id"],
        permission_tier=PermissionTier.P5,
    )


def _effect_counts(container: AppContainer, principal: Principal) -> tuple[int, ...]:
    return (
        len(container.calendar.list_events(principal)),
        len(container.scheduler.list_for_tenant(principal)),
        len(container.event_store.list_for_tenant(principal)),
        len(container.outbox.list_for_tenant(principal)),
    )


@pytest.mark.parametrize(
    ("timezone", "now", "text", "expected"),
    [
        pytest.param(
            "UTC",
            "2026-12-31T23:59:00+00:00",
            "recuérdame mañana a las 00:00 cerrar el año",
            datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
            id="utc-midnight",
        ),
        pytest.param(
            "America/Bogota",
            "2027-01-01T04:59:00+00:00",
            "recuérdame mañana a la medianoche llamar a casa",
            datetime(2027, 1, 1, 5, 0, tzinfo=UTC),
            id="bogota-local-day",
        ),
        pytest.param(
            "America/New_York",
            "2026-03-08T04:59:00+00:00",
            "recuérdame mañana a las 3:00 am revisar el despliegue",
            datetime(2026, 3, 8, 7, 0, tzinfo=UTC),
            id="new-york-after-gap",
        ),
    ],
)
def test_http_preserves_local_calendar_through_approval(
    timezone: str,
    now: str,
    text: str,
    expected: datetime,
) -> None:
    container = _container()
    client = _client(container)

    pending = client.post(
        "/v1/runtime/reminders",
        json=_payload(timezone=timezone, now=now, text=text),
        headers=BASE_HEADERS,
    )

    assert pending.status_code == 202, pending.text
    approval_id = pending.json()["approval"]["approval_id"]
    approved = client.post(
        f"/v1/runtime/approvals/{approval_id}/approve",
        json={},
        headers=BASE_HEADERS,
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["status"] == AgentStatus.completed.value
    [event] = container.calendar.list_events(_principal())
    assert event.starts_at == expected


@pytest.mark.parametrize(
    ("timezone", "now", "text"),
    [
        pytest.param(
            "America/New_York",
            "2026-03-07T12:00:00+00:00",
            "recuérdame mañana a las 2:30 am revisar la alarma",
            id="dst-gap",
        ),
        pytest.param(
            "America/New_York",
            "2026-10-31T12:00:00+00:00",
            "recuérdame mañana a las 1:30 am revisar la alarma",
            id="dst-fold",
        ),
        pytest.param(
            "America/Bogota",
            "2026-06-20T23:00:00+00:00",
            "recuérdame hoy a las 17 cerrar caja",
            id="elapsed-today",
        ),
    ],
)
def test_http_temporal_rejection_has_no_approval_or_effects(
    timezone: str,
    now: str,
    text: str,
) -> None:
    container = _container()
    response = _client(container).post(
        "/v1/runtime/reminders",
        json=_payload(timezone=timezone, now=now, text=text),
        headers=BASE_HEADERS,
    )
    principal = _principal()

    assert response.status_code == 200, response.text
    assert response.json()["status"] == AgentStatus.needs_clarification.value
    assert container.approvals.list_for_tenant(principal) == []
    assert _effect_counts(container, principal) == (0, 0, 0, 0)


def test_http_explicit_source_event_survives_pending_approval_and_app_restart() -> None:
    container = _container()
    client = _client(container)
    payload = _payload(
        message_id="provider-message-42",
        source_event_id="http-event-900",
    )
    expected_key = reminder_idempotency_key(
        tenant_id="tenant-a",
        channel="telegram",
        principal_id="user-1",
        conversation_id="chat-1",
        source_event_id="http-event-900",
    )

    pending_response = client.post(
        "/v1/runtime/reminders", json=payload, headers=BASE_HEADERS
    )

    assert pending_response.status_code == 202, pending_response.text
    pending_body = pending_response.json()
    assert pending_body["run_id"] == expected_key
    approval_id = pending_body["approval"]["approval_id"]
    persisted = container.approvals.get(_principal(), approval_id)
    assert persisted is not None
    assert persisted.message_id == "provider-message-42"
    assert persisted.source_event_id == "http-event-900"
    assert persisted.idempotency_key == expected_key

    restarted_client = _client(container)
    approved = restarted_client.post(
        f"/v1/runtime/approvals/{approval_id}/approve",
        json={},
        headers=BASE_HEADERS,
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["run_id"] == expected_key
    state = container.states.get_by_idempotency_key(_principal(), expected_key)
    assert state is not None
    assert state.status.value == "completed"
    assert _effect_counts(container, _principal()) == (1, 1, 1, 1)


def test_http_same_payload_replays_pending_and_completed_state_once() -> None:
    container = _container()
    client = _client(container)
    payload = _payload()

    first = client.post("/v1/runtime/reminders", json=payload, headers=BASE_HEADERS)
    pending_replay = client.post(
        "/v1/runtime/reminders", json=payload, headers=BASE_HEADERS
    )

    assert first.status_code == pending_replay.status_code == 202
    assert first.json()["run_id"] == pending_replay.json()["run_id"]
    assert (
        first.json()["approval"]["approval_id"]
        == pending_replay.json()["approval"]["approval_id"]
    )
    assert pending_replay.json()["reused"] is True
    assert len(container.approvals.list_for_tenant(_principal())) == 1
    approval_id = first.json()["approval"]["approval_id"]
    approved = client.post(
        f"/v1/runtime/approvals/{approval_id}/approve",
        json={},
        headers=BASE_HEADERS,
    )
    completed_replay = client.post(
        "/v1/runtime/reminders",
        json=_payload(now="2026-06-23T12:00:00+00:00"),
        headers=BASE_HEADERS,
    )

    assert approved.status_code == 200, approved.text
    assert completed_replay.status_code == 200, completed_replay.text
    assert completed_replay.json()["reused"] is True
    assert completed_replay.json()["run_id"] == first.json()["run_id"]
    assert _effect_counts(container, _principal()) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("text", "recuérdame mañana a las 17 cerrar la oficina"),
        ("recipient", "chat-otro"),
        ("timezone", "UTC"),
    ],
)
def test_http_changed_payload_is_a_conflict_without_new_effects(
    field: str, changed: str
) -> None:
    container = _container()
    client = _client(container)
    first = client.post("/v1/runtime/reminders", json=_payload(), headers=BASE_HEADERS)
    assert first.status_code == 202, first.text
    approval_id = first.json()["approval"]["approval_id"]
    approved = client.post(
        f"/v1/runtime/approvals/{approval_id}/approve",
        json={},
        headers=BASE_HEADERS,
    )
    assert approved.status_code == 200, approved.text
    before = _effect_counts(container, _principal())

    conflict = client.post(
        "/v1/runtime/reminders",
        json=_payload(**{field: changed}),
        headers=BASE_HEADERS,
    )

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "conflict"
    assert _effect_counts(container, _principal()) == before == (1, 1, 1, 1)
    assert len(container.approvals.list_for_tenant(_principal())) == 1
    assert len(container.states.list_for_tenant(_principal())) == 1


def test_http_identity_dimensions_do_not_collide() -> None:
    container = _container()
    client = _client(container)
    variants = [
        (BASE_HEADERS, {}),
        (
            {**BASE_HEADERS, "X-Tenant-Id": "tenant-b"},
            {},
        ),
        (
            {**BASE_HEADERS, "X-Principal-Id": "user-2"},
            {},
        ),
        (BASE_HEADERS, {"conversation_id": "chat-2", "recipient": "chat-2"}),
        (BASE_HEADERS, {"channel": "whatsapp"}),
        (BASE_HEADERS, {"source_event_id": "http-event-901"}),
    ]
    run_ids: set[str] = set()

    for headers, overrides in variants:
        response = client.post(
            "/v1/runtime/reminders",
            json=_payload(**overrides),
            headers=headers,
        )

        assert response.status_code == 202, response.text
        run_ids.add(response.json()["run_id"])
        assert _effect_counts(container, _principal(headers)) == (0, 0, 0, 0)

    assert len(run_ids) == len(variants)


def test_http_requires_tenant_header_and_rejects_wrong_telegram_token() -> None:
    container = _container()
    missing_tenant = _client(container).get(
        "/v1/runtime/approvals",
        headers={"X-Principal-Id": "user-1"},
    )

    assert missing_tenant.status_code == 400
    assert missing_tenant.json()["error"]["code"] == "tenant_required"

    telegram_client = TestClient(
        create_app(
            container,
            settings=AppSettings(
                tenant_id="tenant-a",
                reminder_worker_enabled=False,
                telegram_webhook_secret="webhook-secret",
            ),
        )
    )
    wrong_token = telegram_client.post(
        "/webhooks/telegram/webhook-secret",
        json={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-token"},
    )

    assert wrong_token.status_code == 403
    assert wrong_token.json()["error"]["code"] == "permission_denied"


def test_http_approval_filters_and_terminal_decisions_are_explicit() -> None:
    container = _container()
    client = _client(container)
    first_pending = client.post(
        "/v1/runtime/reminders",
        json=_payload(),
        headers=BASE_HEADERS,
    )
    assert first_pending.status_code == 202, first_pending.text
    first_approval_id = first_pending.json()["approval"]["approval_id"]

    filtered = client.get(
        "/v1/runtime/approvals?status=pending",
        headers=BASE_HEADERS,
    )
    assert filtered.status_code == 200
    assert [approval["approval_id"] for approval in filtered.json()] == [
        first_approval_id
    ]

    missing_approve = client.post(
        "/v1/runtime/approvals/missing/approve",
        json={},
        headers=BASE_HEADERS,
    )
    missing_reject = client.post(
        "/v1/runtime/approvals/missing/reject",
        json={},
        headers=BASE_HEADERS,
    )
    assert missing_approve.status_code == missing_reject.status_code == 404

    rejected = client.post(
        f"/v1/runtime/approvals/{first_approval_id}/reject",
        json={},
        headers=BASE_HEADERS,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    approve_rejected = client.post(
        f"/v1/runtime/approvals/{first_approval_id}/approve",
        json={},
        headers=BASE_HEADERS,
    )
    assert approve_rejected.status_code == 409

    second_pending = client.post(
        "/v1/runtime/reminders",
        json=_payload(
            message_id="provider-message-43", source_event_id="http-event-901"
        ),
        headers=BASE_HEADERS,
    )
    assert second_pending.status_code == 202, second_pending.text
    second_approval_id = second_pending.json()["approval"]["approval_id"]
    approved = client.post(
        f"/v1/runtime/approvals/{second_approval_id}/approve",
        json={},
        headers=BASE_HEADERS,
    )
    assert approved.status_code == 200, approved.text

    reject_approved = client.post(
        f"/v1/runtime/approvals/{second_approval_id}/reject",
        json={},
        headers=BASE_HEADERS,
    )
    assert reject_approved.status_code == 409
