"""Boundary tests for reminder creation through the command service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.use_cases.commands import ConversationCommandService
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.reminders.idempotency import ReminderIdempotencyConflict
from personal_assistant.infrastructure.bootstrap import AppContainer, build_container


class _NoNetworkNotificationProvider:
    """A provider fake that proves these tests never send externally."""

    def send(self, principal, request, *, approval=None):  # type: ignore[no-untyped-def]
        raise AssertionError("boundary tests must not call a notification provider")


def _principal(
    *, tenant_id: str = "tenant-a", principal_id: str = "user-1"
) -> Principal:
    return Principal.for_test(
        tenant_id=tenant_id,
        principal_id=principal_id,
        permission_tier=PermissionTier.P5,
    )


def _message(
    *,
    principal: Principal,
    text: str = "recuérdame mañana a las 17 cerrar caja",
    message_id: str = "message-42",
    source_event_id: str = "update-900",
    conversation_id: str = "chat-1",
    channel: ChannelName = ChannelName.telegram,
) -> NormalizedMessage:
    return NormalizedMessage.model_validate(
        {
            "channel": channel,
            "actor_id": principal.principal_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "source_event_id": source_event_id,
            "text": f"/recordar {text}",
            "command": "recordar",
            "command_args": text,
        }
    )


def _container() -> AppContainer:
    return build_container(
        llm=None,
        notifications=_NoNetworkNotificationProvider(),
    )


def _effect_counts(container: AppContainer, principal: Principal) -> tuple[int, ...]:
    return (
        len(container.calendar.list_events(principal)),
        len(container.scheduler.list_for_tenant(principal)),
        len(container.event_store.list_for_tenant(principal)),
        len(container.outbox.list_for_tenant(principal)),
    )


def _restarted_commands(container: AppContainer) -> ConversationCommandService:
    restarted_workflow = ReminderWorkflow(
        calendar=container.calendar,
        scheduler=container.scheduler,
        event_store=container.event_store,
        outbox=container.outbox,
        states=container.states,
        traces=container.traces,
        llm=None,
        prompt_catalog=container.prompt_catalog,
    )
    return ConversationCommandService(
        approvals=container.approvals,
        calendar=container.calendar,
        reminder_workflow=restarted_workflow,
        states=container.states,
        event_store=container.event_store,
        outbox=container.outbox,
        llm=None,
        prompt_catalog=container.prompt_catalog,
        traces=container.traces,
    )


@pytest.mark.parametrize(
    ("timezone", "now", "text"),
    [
        pytest.param(
            "America/New_York",
            datetime(2026, 3, 7, 12, tzinfo=UTC),
            "recuérdame mañana a las 2:30 am revisar la alarma",
            id="dst-gap",
        ),
        pytest.param(
            "America/New_York",
            datetime(2026, 10, 31, 12, tzinfo=UTC),
            "recuérdame mañana a las 1:30 am revisar la alarma",
            id="dst-fold",
        ),
        pytest.param(
            "America/Bogota",
            datetime(2026, 6, 20, 23, tzinfo=UTC),
            "recuérdame hoy a las 17 cerrar caja",
            id="elapsed-today",
        ),
    ],
)
def test_temporal_ambiguity_stops_before_approval_and_effects(
    timezone: str,
    now: datetime,
    text: str,
) -> None:
    container = _container()
    principal = _principal()

    result = container.commands.handle(
        principal,
        _message(principal=principal, text=text),
        now=now,
        timezone=timezone,
    )

    assert result.status == AgentStatus.needs_clarification
    assert container.approvals.list_for_tenant(principal) == []
    assert _effect_counts(container, principal) == (0, 0, 0, 0)


def test_explicit_source_event_survives_pending_approval_and_service_restart() -> None:
    container = _container()
    principal = _principal()
    message = _message(
        principal=principal,
        message_id="provider-message-42",
        source_event_id="provider-update-900",
        text="recuérdame mañana a la medianoche cerrar caja",
    )
    expected_key = reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel=message.channel.value,
        principal_id=principal.principal_id,
        conversation_id=message.conversation_id,
        source_event_id="provider-update-900",
    )

    pending_result = container.commands.handle(
        principal,
        message,
        now=datetime(2026, 12, 31, 4, 59, tzinfo=UTC),
        timezone="America/Bogota",
    )

    assert pending_result.status == AgentStatus.escalated
    assert pending_result.approval_id is not None
    pending = container.approvals.get(principal, pending_result.approval_id)
    assert pending is not None
    assert pending.message_id == "provider-message-42"
    assert pending.source_event_id == "provider-update-900"
    assert pending.timezone == "America/Bogota"
    assert pending.idempotency_key == expected_key
    assert _effect_counts(container, principal) == (0, 0, 0, 0)

    restarted = _restarted_commands(container)
    approved = restarted.handle(
        principal,
        NormalizedMessage.model_validate(
            {
                "channel": "telegram",
                "actor_id": principal.principal_id,
                "conversation_id": "chat-1",
                "message_id": "approval-message-43",
                "source_event_id": "approval-update-901",
                "text": f"/aprobar {pending.approval_id}",
                "command": "aprobar",
                "command_args": pending.approval_id,
            }
        ),
        now=datetime(2026, 12, 31, 5, 1, tzinfo=UTC),
        timezone="UTC",
    )

    assert approved.status == AgentStatus.completed
    state = container.states.get_by_idempotency_key(principal, expected_key)
    assert state is not None
    assert state.idempotency_key == expected_key
    assert state.status.value == "completed"
    assert _effect_counts(container, principal) == (1, 1, 1, 1)
    [event] = container.calendar.list_events(principal)
    assert event.starts_at == datetime(2026, 12, 31, 5, 0, tzinfo=UTC)


def test_completed_replay_is_reused_and_changed_payload_has_no_new_effects() -> None:
    container = _container()
    principal = _principal()
    original = _message(
        principal=principal,
        text="recuérdame mañana a las 17 cerrar caja",
    )
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    pending = container.commands.handle(
        principal, original, now=now, timezone="America/Bogota"
    )
    assert pending.approval_id is not None
    approved = container.commands.handle(
        principal,
        NormalizedMessage.model_validate(
            {
                "channel": "telegram",
                "actor_id": principal.principal_id,
                "conversation_id": "chat-1",
                "message_id": "approval-message",
                "source_event_id": "approval-update",
                "text": f"/aprobar {pending.approval_id}",
                "command": "aprobar",
                "command_args": pending.approval_id,
            }
        ),
        now=now,
        timezone="America/Bogota",
    )
    assert approved.status == AgentStatus.completed
    completed_effects = _effect_counts(container, principal)

    replay = container.commands.handle(
        principal,
        original,
        now=now + timedelta(days=3),
        timezone="America/Bogota",
    )

    assert replay.status == AgentStatus.completed
    assert _effect_counts(container, principal) == completed_effects == (1, 1, 1, 1)
    assert len(container.approvals.list_for_tenant(principal)) == 1

    altered = _message(
        principal=principal,
        text="recuérdame mañana a las 17 cerrar la oficina",
    )
    with pytest.raises(ReminderIdempotencyConflict):
        container.commands.handle(
            principal,
            altered,
            now=now,
            timezone="America/Bogota",
        )

    assert _effect_counts(container, principal) == completed_effects
    assert len(container.approvals.list_for_tenant(principal)) == 1
    assert len(container.states.list_for_tenant(principal)) == 1


def test_each_actor_conversation_channel_and_source_event_gets_a_distinct_key() -> None:
    container = _container()
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)
    identities = [
        (_principal(), "chat-1", ChannelName.telegram, "update-1"),
        (
            _principal(tenant_id="tenant-b"),
            "chat-1",
            ChannelName.telegram,
            "update-1",
        ),
        (
            _principal(principal_id="user-2"),
            "chat-1",
            ChannelName.telegram,
            "update-1",
        ),
        (_principal(), "chat-2", ChannelName.telegram, "update-1"),
        (_principal(), "chat-1", ChannelName.whatsapp, "update-1"),
        (_principal(), "chat-1", ChannelName.telegram, "update-2"),
    ]
    observed_keys: set[str] = set()

    for principal, conversation_id, channel, source_event_id in identities:
        message = _message(
            principal=principal,
            conversation_id=conversation_id,
            channel=channel,
            source_event_id=source_event_id,
        )
        result = container.commands.handle(
            principal,
            message,
            now=now,
            timezone="UTC",
        )
        expected_key = reminder_idempotency_key(
            tenant_id=principal.tenant_id,
            channel=channel.value,
            principal_id=principal.principal_id,
            conversation_id=conversation_id,
            source_event_id=source_event_id,
        )

        assert result.status == AgentStatus.escalated
        assert (
            container.states.get_by_idempotency_key(principal, expected_key) is not None
        )
        observed_keys.add(expected_key)

    assert len(observed_keys) == len(identities)
    for principal, _, _, _ in identities:
        assert _effect_counts(container, principal) == (0, 0, 0, 0)
