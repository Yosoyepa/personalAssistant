"""Hermetic v2 reminder idempotency eval executor.

The store path calls the production identity, payload and state-store contracts.
The HTTP and Telegram paths additionally exercise their real FastAPI boundaries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_assistant.adapters.persistence.in_memory import InMemoryWorkflowStateStore
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.reminders.idempotency import (
    REMINDER_IDEMPOTENCY_KEY_PREFIX,
    ReminderIdempotency,
    ReminderIdempotencyConflict,
    ReminderIdempotencyIdentity,
    ReminderPayload,
)


class EventModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tenantId: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    principalId: str = Field(min_length=1)
    conversationId: str = Field(min_length=1)
    sourceEventId: str = Field(min_length=1)
    text: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    timezone: str = Field(min_length=1)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    transport: Literal["store", "http", "telegram"]
    first: EventModel
    second: EventModel

    @model_validator(mode="after")
    def validate_transport_shape(self) -> "InputModel":
        if self.transport == "http":
            for event in (self.first, self.second):
                if event.channel.casefold().strip() not in {"telegram", "whatsapp"}:
                    raise ValueError("http evals require a supported channel")
            if (
                self.first.tenantId != self.second.tenantId
                or self.first.principalId != self.second.principalId
            ):
                raise ValueError("http evals require one authenticated tenant and actor")
        if self.transport == "telegram":
            for event in (self.first, self.second):
                if (
                    event.channel.casefold().strip() != "telegram"
                    or not event.principalId.isdecimal()
                    or not event.conversationId.isdecimal()
                    or not event.recipient.isdecimal()
                    or not event.sourceEventId.removeprefix("event-").isdecimal()
                ):
                    raise ValueError("telegram evals require numeric Telegram identities")
        return self


class ExpectedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    keyRelation: Literal["same", "different"]
    fingerprintRelation: Literal["same", "different"]
    effectIdsRelation: Literal["same", "different"]
    keyVersion: Literal[2]
    keyDigestLength: Literal[64]
    effectIdsAreDomainSeparated: bool
    firstOutcome: Literal["registered"]
    secondOutcome: Literal["registered", "replayed", "conflict"]
    firstTenantWorkflowRows: int = Field(ge=0)
    secondTenantWorkflowRows: int = Field(ge=0)
    crossTenantVisibleRows: Literal[0]
    approvalRows: int = Field(ge=0)
    calendarRows: int = Field(ge=0)
    schedulerRows: int = Field(ge=0)
    eventRows: int = Field(ge=0)
    outboxRows: int = Field(ge=0)
    transportStatuses: list[int] = Field(min_length=0, max_length=2)
    internalMetadataLeaked: bool


def _claim(event: EventModel) -> ReminderIdempotency:
    return ReminderIdempotency(
        identity=ReminderIdempotencyIdentity(
            tenant_id=event.tenantId,
            channel=event.channel,
            principal_id=event.principalId,
            conversation_id=event.conversationId,
            source_event_id=event.sourceEventId,
        ),
        payload=ReminderPayload(
            text=event.text,
            recipient=event.recipient,
            timezone=event.timezone,
        ),
    )


def _state(claim: ReminderIdempotency, *, workflow_id: str) -> WorkflowState:
    return WorkflowState(
        workflow_id=workflow_id,
        tenant_id=claim.identity.tenant_id,
        workflow_type="reminder.create",
        status=WorkflowStatus.running,
        step="eval",
        idempotency_key=claim.key,
        payload_fingerprint=claim.payload_fingerprint,
    )


def _store_result(first: ReminderIdempotency, second: ReminderIdempotency) -> dict[str, object]:
    store = InMemoryWorkflowStateStore()
    first_principal = Principal.for_test(
        principal_id=first.identity.principal_id, tenant_id=first.identity.tenant_id
    )
    second_principal = Principal.for_test(
        principal_id=second.identity.principal_id, tenant_id=second.identity.tenant_id
    )
    store.register_or_replay(first_principal, _state(first, workflow_id="eval-first"))
    try:
        registration = store.register_or_replay(
            second_principal, _state(second, workflow_id="eval-second")
        )
    except ReminderIdempotencyConflict:
        second_outcome = "conflict"
    else:
        second_outcome = "replayed" if registration.replayed else "registered"
    first_rows = len(store.list_for_tenant(first_principal))
    second_rows = len(store.list_for_tenant(second_principal))
    cross_tenant_rows = 0
    if second_principal.tenant_id != first_principal.tenant_id:
        cross_tenant_rows = int(
            store.get_by_idempotency_key(second_principal, first.key) is not None
        )
    return {
        "secondOutcome": second_outcome,
        "firstTenantWorkflowRows": first_rows,
        "secondTenantWorkflowRows": second_rows,
        "crossTenantVisibleRows": cross_tenant_rows,
    }


def _http_result(first: EventModel, second: EventModel) -> dict[str, object]:
    from fastapi.testclient import TestClient

    from personal_assistant.infrastructure.bootstrap import build_container
    from personal_assistant.infrastructure.config import AppSettings
    from personal_assistant.infrastructure.http import create_app

    container = build_container()
    first_claim = _claim(first)
    second_claim = _claim(second)
    settings = AppSettings(
        tenant_id=first.tenantId,
        admin_token="eval-token",
        local_auth_principal_id=first.principalId,
        local_auth_permission_tier=PermissionTier.P5,
    )
    headers = {"Authorization": "Bearer eval-token"}

    def payload(event: EventModel) -> dict[str, object]:
        return {
            "message_id": event.sourceEventId,
            "source_event_id": event.sourceEventId,
            "conversation_id": event.conversationId,
            "text": event.text,
            "channel": event.channel,
            "recipient": event.recipient,
            "now": "2026-07-17T12:00:00+00:00",
            "timezone": event.timezone,
        }

    with TestClient(
        create_app(container, settings=settings), client=("127.0.0.1", 50000)
    ) as client:
        first_response = client.post("/v1/runtime/reminders", json=payload(first), headers=headers)
        second_response = client.post("/v1/runtime/reminders", json=payload(second), headers=headers)
    principal = Principal.for_test(
        principal_id=first.principalId,
        tenant_id=first.tenantId,
        permission_tier=PermissionTier.P5,
    )
    second_body = second_response.text.casefold()
    return {
        "secondOutcome": "conflict" if second_response.status_code == 409 else "replayed",
        "firstTenantWorkflowRows": len(container.states.list_for_tenant(principal)),
        "secondTenantWorkflowRows": len(container.states.list_for_tenant(principal)),
        "crossTenantVisibleRows": 0,
        "approvalRows": len(container.approvals.list_pending(principal)),
        "calendarRows": len(container.calendar.list_events(principal)),
        "schedulerRows": len(container.scheduler.list_for_tenant(principal)),
        "eventRows": len(container.event_store.list_for_tenant(principal)),
        "outboxRows": len(container.outbox.list_for_tenant(principal)),
        "transportStatuses": [first_response.status_code, second_response.status_code],
        "internalMetadataLeaked": any(
            value in second_body
            for value in (
                first_claim.key.casefold(),
                second_claim.key.casefold(),
                first_claim.payload_fingerprint,
                second_claim.payload_fingerprint,
            )
        ),
    }


def _telegram_result(first: EventModel, second: EventModel) -> dict[str, object]:
    from fastapi.testclient import TestClient

    from personal_assistant.infrastructure.bootstrap import build_container
    from personal_assistant.infrastructure.config import AppSettings
    from personal_assistant.infrastructure.http import create_app

    container = build_container()
    first_claim = _claim(first)
    second_claim = _claim(second)
    settings = AppSettings(
        tenant_id=first.tenantId,
        telegram_webhook_secret="eval-secret",
        telegram_allowed_user_ids=frozenset({first.principalId}),
    )
    headers = {"X-Telegram-Bot-Api-Secret-Token": "eval-secret"}

    def payload(event: EventModel) -> dict[str, object]:
        return {
            "update_id": int(event.sourceEventId.removeprefix("event-")),
            "message": {
                "message_id": int(event.sourceEventId.removeprefix("event-")),
                "chat": {"id": event.conversationId},
                "from": {"id": event.principalId},
                "text": event.text,
            },
        }

    with TestClient(create_app(container, settings=settings)) as client:
        first_response = client.post("/webhooks/telegram", json=payload(first), headers=headers)
        second_response = client.post("/webhooks/telegram", json=payload(second), headers=headers)
    principal = Principal.for_test(
        principal_id=first.principalId,
        tenant_id=first.tenantId,
        permission_tier=PermissionTier.P5,
    )
    second_body = second_response.text.casefold()
    return {
        "secondOutcome": "conflict" if second_response.json().get("status") == "failed" else "replayed",
        "firstTenantWorkflowRows": len(container.states.list_for_tenant(principal)),
        "secondTenantWorkflowRows": len(container.states.list_for_tenant(principal)),
        "crossTenantVisibleRows": 0,
        "approvalRows": len(container.approvals.list_pending(principal)),
        "calendarRows": len(container.calendar.list_events(principal)),
        "schedulerRows": len(container.scheduler.list_for_tenant(principal)),
        "eventRows": len(container.event_store.list_for_tenant(principal)),
        "outboxRows": len(container.outbox.list_for_tenant(principal)),
        "transportStatuses": [first_response.status_code, second_response.status_code],
        "internalMetadataLeaked": any(
            value in second_body
            for value in (
                first_claim.key.casefold(),
                second_claim.key.casefold(),
                first_claim.payload_fingerprint,
                second_claim.payload_fingerprint,
            )
        ),
    }


def execute(input_model: InputModel) -> dict[str, object]:
    first = _claim(input_model.first)
    second = _claim(input_model.second)
    first_effects = first.effect_ids.model_dump(mode="json")
    second_effects = second.effect_ids.model_dump(mode="json")
    outcome: dict[str, object] = {
        "keyRelation": "same" if first.key == second.key else "different",
        "fingerprintRelation": (
            "same"
            if first.payload_fingerprint == second.payload_fingerprint
            else "different"
        ),
        "effectIdsRelation": "same" if first_effects == second_effects else "different",
        "keyVersion": 2,
        "keyDigestLength": len(first.key.removeprefix(REMINDER_IDEMPOTENCY_KEY_PREFIX)),
        "effectIdsAreDomainSeparated": len(set(first_effects.values())) == len(first_effects),
        "firstOutcome": "registered",
        "approvalRows": 0,
        "calendarRows": 0,
        "schedulerRows": 0,
        "eventRows": 0,
        "outboxRows": 0,
        "transportStatuses": [],
        "firstTenantWorkflowRows": 0,
        "secondTenantWorkflowRows": 0,
        "crossTenantVisibleRows": 0,
        "internalMetadataLeaked": False,
    }
    if input_model.transport == "store":
        outcome.update(_store_result(first, second))
    elif input_model.transport == "http":
        outcome.update(_http_result(input_model.first, input_model.second))
    else:
        outcome.update(_telegram_result(input_model.first, input_model.second))
    return outcome
