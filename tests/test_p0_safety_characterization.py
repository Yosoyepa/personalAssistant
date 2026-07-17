"""Strict characterization tests for known P0 runtime safety gaps.

Each xfail is limited to ``AssertionError`` so an unexpected exception remains a
real failure instead of being hidden by the known-gap marker.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.notifications.telegram import TelegramNotificationTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.application.use_cases.reminder_notifications import DispatchDueReminders
from personal_assistant.application.use_cases.reminders import ReminderWorkflow, reminder_idempotency_key
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import create_app
from personal_assistant.infrastructure.worker import ReminderWorker, RuntimeNotificationApprovalPolicy


NOW = datetime(2026, 6, 20, 12, tzinfo=UTC)


class SimulatedProcessCrash(RuntimeError):
    """Failure injected at an otherwise non-atomic process boundary."""


class CrashBeforeTerminalStateStore(InMemoryWorkflowStateStore):
    """Crash once after side effects, immediately before completed state persists."""

    def __init__(self) -> None:
        super().__init__()
        self._crash_once = True

    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        if self._crash_once and state.status == WorkflowStatus.completed:
            self._crash_once = False
            raise SimulatedProcessCrash("crash before terminal workflow state")
        return super().upsert(principal, state)


class RacingReminderScheduler(ReminderScheduler):
    """Force two workers to observe the same scheduler snapshot."""

    def __init__(self) -> None:
        super().__init__()
        self._due_barrier = Barrier(2)

    def due(self, principal: Principal, now: datetime) -> list[ScheduledReminder]:
        jobs = super().due(principal, now)
        self._due_barrier.wait(timeout=5)
        return jobs


class CrashOnceBeforeMarkSentScheduler(ReminderScheduler):
    """Model a process death after provider delivery but before local acknowledgement."""

    def __init__(self) -> None:
        super().__init__()
        self._crash_once = True

    def mark_sent(self, principal: Principal, reminder_id: str) -> ScheduledReminder:
        if self._crash_once:
            self._crash_once = False
            raise SimulatedProcessCrash("crash after provider delivery")
        return super().mark_sent(principal, reminder_id)


class RecordingTelegramClient:
    """Hermetic Telegram stand-in whose provider sends are intentionally non-idempotent."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.messages: list[tuple[str, str]] = []

    def send_message(self, *, chat_id: str, text: str) -> dict[str, int]:
        with self._lock:
            self.messages.append((chat_id, text))
            message_id = len(self.messages)
        return {"message_id": message_id}

    def send_audio(self, **_: object) -> dict[str, int]:
        raise RuntimeError("audio delivery is outside this characterization")


def principal() -> Principal:
    return Principal.for_test(
        principal_id="user-1",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )


def approved_request(
    actor: Principal,
    *,
    message_id: str,
    text: str,
    timezone: str = "UTC",
) -> ReminderWorkflowInput:
    key = reminder_idempotency_key(actor.tenant_id, message_id, text)
    approval = ApprovalGrant.issue(
        principal=actor,
        action="calendar.create_event",
        resource=f"{key}:calendar",
        tier=PermissionTier.P3,
    )
    return ReminderWorkflowInput(
        message_id=message_id,
        conversation_id="chat-1",
        text=text,
        recipient="chat-1",
        now=NOW,
        timezone=timezone,
        idempotency_key=key,
        approval=approval,
    )


def worker(
    scheduler: ReminderScheduler,
    client: RecordingTelegramClient,
) -> ReminderWorker:
    return ReminderWorker(
        dispatcher=DispatchDueReminders(
            scheduler=scheduler,
            # A fresh adapter models another process or a restarted process.
            notifications=TelegramNotificationTool(client),
        ),
        approval_policy=RuntimeNotificationApprovalPolicy(
            approve_notifications=True,
            approval_ttl=None,
        ),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def schedule_due(scheduler: ReminderScheduler, actor: Principal, *, key: str) -> None:
    scheduler.schedule_before_event(
        actor,
        calendar_event_id=f"calendar-{key}",
        starts_at=NOW,
        channel="telegram",
        recipient="chat-1",
        body="Recordatorio hermetico",
        minutes_before=0,
        idempotency_key=key,
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "P0 atomicity gap: retry after the outbox write creates a new event fingerprint "
        "and conflicts before terminal workflow state is persisted"
    ),
)
def test_retry_after_crash_between_outbox_and_terminal_state_is_atomic() -> None:
    actor = principal()
    calendar = LocalCalendarTool()
    scheduler = ReminderScheduler()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    states = CrashBeforeTerminalStateStore()
    workflow = ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=TraceRecorder(),
    )
    request = approved_request(
        actor,
        message_id="atomic-crash-1",
        text="recuerdame clase el martes a las 17",
    )

    with pytest.raises(SimulatedProcessCrash, match="before terminal workflow state"):
        workflow.run(actor, request)

    retry_result = None
    retry_conflict: AssistantError | None = None
    try:
        retry_result = workflow.run(actor, request)
    except AssistantError as exc:
        if exc.code != ErrorCode.CONFLICT:
            raise
        retry_conflict = exc

    assert retry_conflict is None, "retry must resume or replay atomically, not conflict with its own outbox event"
    assert retry_result is not None
    assert retry_result.status == AgentStatus.completed
    assert len(calendar.list_events(actor)) == 1
    assert len(scheduler.list_for_tenant(actor)) == 1
    assert len(event_store.list_for_tenant(actor)) == 1
    assert len(outbox.list_for_tenant(actor)) == 1
    terminal = states.get_by_idempotency_key(actor, request.idempotency_key or "")
    assert terminal is not None
    assert terminal.status == WorkflowStatus.completed


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P0 timezone gap: ReminderWorkflow parses wall-clock text using request.now tzinfo and ignores request.timezone",
)
def test_request_timezone_controls_wall_clock_interpretation() -> None:
    actor = principal()
    container = build_container()
    request = approved_request(
        actor,
        message_id="timezone-1",
        text="recuerdame clase el martes a las 17",
        timezone="America/Bogota",
    )

    result = container.reminder_workflow.run(actor, request)

    if result.status != AgentStatus.completed:
        raise RuntimeError(f"timezone fixture did not complete: {result.status}")
    [event] = container.calendar.list_events(actor)
    # 17:00 in America/Bogota is 22:00 UTC; the input `now` being UTC must not
    # silently reinterpret the requested local wall clock as 17:00 UTC.
    assert event.starts_at.astimezone(UTC) == datetime(2026, 6, 23, 22, tzinfo=UTC)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P0 auth gap: the HTTP boundary marks caller-controlled identity and permission headers as verified authentication",
)
def test_http_runtime_rejects_forged_principal_headers() -> None:
    app = create_app(
        build_container(),
        settings=AppSettings(tenant_id="configured-tenant", reminder_worker_enabled=False),
    )
    forged_headers = {
        "X-Principal-Id": "attacker",
        "X-Tenant-Id": "victim-tenant",
        "X-Permission-Tier": "P6",
        "X-Scopes": "*",
    }
    payload = {
        "message_id": "forged-auth-1",
        "conversation_id": "chat-1",
        "text": "recuerdame clase el martes a las 17",
        "channel": "telegram",
        "recipient": "chat-1",
        "now": NOW.isoformat(),
        "timezone": "America/Bogota",
    }

    with TestClient(app) as client:
        response = client.post("/v1/runtime/reminders", json=payload, headers=forged_headers)

    assert response.status_code in {401, 403}, (
        "unverified X-Principal-Id/X-Tenant-Id/X-Permission-Tier headers must never create a trusted principal"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P0 worker gap: due reminders have no atomic claim, so two workers can deliver the same notification",
)
def test_two_workers_cannot_deliver_the_same_due_reminder() -> None:
    actor = principal()
    scheduler = RacingReminderScheduler()
    client = RecordingTelegramClient()
    schedule_due(scheduler, actor, key="two-workers-1")
    workers = [worker(scheduler, client), worker(scheduler, client)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(instance.run_once, actor, now=NOW) for instance in workers]
        ticks = [future.result(timeout=5) for future in futures]

    assert sum(tick.sent_count for tick in ticks) == 1, "only the worker holding the durable claim may send"
    assert len(client.messages) == 1, "the provider must observe exactly one delivery"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "P0 worker durability gap: adapter idempotency is process-local, so restart after provider send "
        "but before mark_sent redelivers"
    ),
)
def test_worker_restart_after_provider_send_does_not_redeliver() -> None:
    actor = principal()
    scheduler = CrashOnceBeforeMarkSentScheduler()
    client = RecordingTelegramClient()
    schedule_due(scheduler, actor, key="restart-1")

    with pytest.raises(SimulatedProcessCrash, match="after provider delivery"):
        worker(scheduler, client).run_once(actor, now=NOW)

    restarted_tick = worker(scheduler, client).run_once(actor, now=NOW)

    if restarted_tick.sent_count > 1:
        raise RuntimeError("one worker tick reported more than one send for one due reminder")
    assert len(client.messages) == 1, "a durable idempotency record must survive the worker process"
    assert scheduler.due(actor, NOW) == []
