from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.in_memory_uow import (
    InMemoryReminderUnitOfWork,
)
from personal_assistant.application.dto.events import CloudEvent
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.application.ports.calendar import CalendarEventResult
from personal_assistant.application.ports.events import OutboxMessage
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container


NOW = datetime(2026, 6, 20, 12, tzinfo=UTC)


class InjectedWriteFailure(RuntimeError):
    pass


@dataclass
class FaultPlan:
    point: str
    enabled: bool = True
    fired: bool = False

    def hit(self, point: str) -> None:
        if self.enabled and not self.fired and self.point == point:
            self.fired = True
            raise InjectedWriteFailure(point)


class FaultCalendar(LocalCalendarTool):
    def __init__(self, plan: FaultPlan) -> None:
        super().__init__()
        self.plan = plan

    def create_event(self, *args, **kwargs) -> CalendarEventResult:
        result = super().create_event(*args, **kwargs)
        self.plan.hit("calendar")
        return result


class FaultScheduler(ReminderScheduler):
    def __init__(self, plan: FaultPlan) -> None:
        super().__init__()
        self.plan = plan

    def schedule_before_event(self, *args, **kwargs) -> ScheduledReminder:
        result = super().schedule_before_event(*args, **kwargs)
        self.plan.hit("scheduler")
        return result


class FaultEventStore(InMemoryEventStore):
    def __init__(self, plan: FaultPlan) -> None:
        super().__init__()
        self.plan = plan

    def append(self, principal: Principal, event: CloudEvent) -> CloudEvent:
        result = super().append(principal, event)
        self.plan.hit("event_store")
        return result


class FaultOutbox(InMemoryOutbox):
    def __init__(self, plan: FaultPlan) -> None:
        super().__init__()
        self.plan = plan

    def add(self, *args, **kwargs) -> OutboxMessage:
        result = super().add(*args, **kwargs)
        self.plan.hit("outbox")
        return result


class FaultWorkflowStates(InMemoryWorkflowStateStore):
    def __init__(self, plan: FaultPlan) -> None:
        super().__init__()
        self.plan = plan

    def register_or_replay(self, *args, **kwargs):
        result = super().register_or_replay(*args, **kwargs)
        if not result.replayed:
            self.plan.hit("workflow_registered")
        return result

    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        result = super().upsert(principal, state)
        if state.status == WorkflowStatus.completed:
            self.plan.hit("workflow_completed")
        return result


@dataclass
class Bundle:
    plan: FaultPlan
    principal: Principal
    calendar: FaultCalendar
    scheduler: FaultScheduler
    event_store: FaultEventStore
    outbox: FaultOutbox
    states: FaultWorkflowStates
    traces: TraceRecorder
    unit_of_work: InMemoryReminderUnitOfWork
    workflow: ReminderWorkflow


def build_bundle(point: str) -> Bundle:
    plan = FaultPlan(point)
    principal = Principal.for_test(
        principal_id="user-1",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )
    calendar = FaultCalendar(plan)
    scheduler = FaultScheduler(plan)
    event_store = FaultEventStore(plan)
    outbox = FaultOutbox(plan)
    states = FaultWorkflowStates(plan)
    traces = TraceRecorder()
    unit_of_work = InMemoryReminderUnitOfWork(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
    )
    workflow = ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
        unit_of_work=unit_of_work,
    )
    return Bundle(
        plan=plan,
        principal=principal,
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
        unit_of_work=unit_of_work,
        workflow=workflow,
    )


def request(bundle: Bundle, source_event_id: str, *, approved: bool = True):
    key = reminder_idempotency_key(
        tenant_id=bundle.principal.tenant_id,
        channel="telegram",
        principal_id=bundle.principal.principal_id,
        conversation_id="chat-1",
        source_event_id=source_event_id,
    )
    approval = None
    if approved:
        approval = ApprovalGrant.issue(
            principal=bundle.principal,
            action="calendar.create_event",
            resource=f"{key}:calendar",
            tier=PermissionTier.P3,
        )
    return ReminderWorkflowInput(
        message_id=source_event_id,
        source_event_id=source_event_id,
        conversation_id="chat-1",
        text="recuérdame clase el martes a las 17",
        recipient="chat-1",
        now=NOW,
        timezone="America/Bogota",
        idempotency_key=key,
        approval=approval,
    )


def business_snapshot(bundle: Bundle) -> dict[str, list[dict]]:
    return {
        "calendar": [
            value.model_dump(mode="json")
            for value in bundle.calendar.list_events(bundle.principal)
        ],
        "scheduler": [
            value.model_dump(mode="json")
            for value in bundle.scheduler.list_for_tenant(bundle.principal)
        ],
        "events": [
            value.model_dump(mode="json")
            for value in bundle.event_store.list_for_tenant(bundle.principal)
        ],
        "outbox": [
            value.model_dump(mode="json")
            for value in bundle.outbox.list_for_tenant(bundle.principal)
        ],
        "states": [
            value.model_dump(mode="json")
            for value in bundle.states.list_for_tenant(bundle.principal)
        ],
    }


@pytest.mark.parametrize(
    "point",
    [
        "workflow_registered",
        "calendar",
        "scheduler",
        "event_store",
        "outbox",
        "workflow_completed",
    ],
)
def test_failure_after_each_write_rolls_back_and_retry_commits_once(point: str) -> None:
    bundle = build_bundle(point)
    approved = request(bundle, f"fault-{point}")

    with pytest.raises(InjectedWriteFailure, match=point):
        bundle.workflow.run(bundle.principal, approved)

    assert business_snapshot(bundle) == {
        "calendar": [],
        "scheduler": [],
        "events": [],
        "outbox": [],
        "states": [],
    }
    trace_types = [
        trace.event_type for trace in bundle.traces.list_for_tenant(bundle.principal)
    ]
    assert TraceEventType.tool_called not in trace_types
    assert TraceEventType.agent_completed not in trace_types

    completed = bundle.workflow.run(bundle.principal, approved)
    replay = bundle.workflow.run(bundle.principal, approved)

    assert completed.status == AgentStatus.completed
    assert replay.status == AgentStatus.completed
    assert replay.reused
    assert replay.calendar_event_id == completed.calendar_event_id
    assert replay.reminder_id == completed.reminder_id
    assert len(bundle.calendar.list_events(bundle.principal)) == 1
    assert len(bundle.scheduler.list_for_tenant(bundle.principal)) == 1
    assert len(bundle.event_store.list_for_tenant(bundle.principal)) == 1
    [message] = bundle.outbox.list_for_tenant(bundle.principal)
    assert message.event.type == "notification.requested"
    assert (
        message.next_attempt_at
        == bundle.scheduler.list_for_tenant(bundle.principal)[0].notify_at
    )
    [terminal] = bundle.states.list_for_tenant(bundle.principal)
    assert terminal.status == WorkflowStatus.completed


def test_rollback_preserves_preexisting_business_state_exactly() -> None:
    bundle = build_bundle("workflow_completed")
    bundle.plan.enabled = False
    bundle.workflow.run(bundle.principal, request(bundle, "preexisting"))
    before = business_snapshot(bundle)
    bundle.plan.enabled = True

    with pytest.raises(InjectedWriteFailure, match="workflow_completed"):
        bundle.workflow.run(bundle.principal, request(bundle, "will-rollback"))

    assert business_snapshot(bundle) == before


def test_failed_resume_restores_exact_waiting_approval_state() -> None:
    bundle = build_bundle("calendar")
    pending_request = request(bundle, "waiting", approved=False)
    pending = bundle.workflow.run(bundle.principal, pending_request)
    assert pending.approval_required
    waiting = bundle.states.get_by_idempotency_key(
        bundle.principal, pending_request.idempotency_key or ""
    )
    assert waiting is not None
    before = waiting.model_dump(mode="json")
    approved = request(bundle, "waiting")

    with pytest.raises(InjectedWriteFailure, match="calendar"):
        bundle.workflow.run(bundle.principal, approved)

    restored = bundle.states.get_by_idempotency_key(
        bundle.principal, approved.idempotency_key or ""
    )
    assert restored is not None
    assert restored.model_dump(mode="json") == before
    assert restored.status == WorkflowStatus.waiting_approval
    assert bundle.calendar.list_events(bundle.principal) == []


def test_exit_without_commit_rolls_back_registered_state() -> None:
    bundle = build_bundle("unused")
    candidate = WorkflowState(
        tenant_id=bundle.principal.tenant_id,
        workflow_type="reminder.create",
        idempotency_key="no-commit",
        payload_fingerprint="a" * 64,
    )

    with bundle.unit_of_work.begin(bundle.principal) as transaction:
        transaction.states.register_or_replay(bundle.principal, candidate)

    assert bundle.states.list_for_tenant(bundle.principal) == []


def test_commit_followed_by_exception_rolls_back_registered_state() -> None:
    bundle = build_bundle("unused")
    candidate = WorkflowState(
        tenant_id=bundle.principal.tenant_id,
        workflow_type="reminder.create",
        idempotency_key="commit-then-fail",
        payload_fingerprint="b" * 64,
    )

    with pytest.raises(RuntimeError, match="after commit"):
        with bundle.unit_of_work.begin(bundle.principal) as transaction:
            transaction.states.register_or_replay(bundle.principal, candidate)
            transaction.commit()
            raise RuntimeError("after commit")

    assert bundle.states.list_for_tenant(bundle.principal) == []


def test_memory_container_injects_its_unit_of_work_into_reminder_workflow() -> None:
    container = build_container()

    assert container.reminder_uow is not None
    assert container.reminder_workflow.unit_of_work is container.reminder_uow


class OrderedTraceRecorder(TraceRecorder):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    def write(self, event: TraceEvent) -> None:
        super().write(event)
        self.order.append(event.event_type.value)


class OrderedTransaction:
    def __init__(self, inner, order: list[str]) -> None:
        self.inner = inner
        self.order = order

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        return self.inner.__exit__(*args)

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def commit(self) -> None:
        self.inner.commit()
        self.order.append("commit")

    def rollback(self) -> None:
        self.inner.rollback()


class OrderedUnitOfWork:
    def __init__(self, inner: InMemoryReminderUnitOfWork, order: list[str]) -> None:
        self.inner = inner
        self.order = order

    def begin(self, principal: Principal):
        return OrderedTransaction(self.inner.begin(principal), self.order)


def test_terminal_traces_are_written_only_after_commit() -> None:
    bundle = build_bundle("unused")
    order: list[str] = []
    bundle.traces = OrderedTraceRecorder(order)
    bundle.workflow.traces = bundle.traces
    bundle.workflow.unit_of_work = OrderedUnitOfWork(bundle.unit_of_work, order)

    result = bundle.workflow.run(bundle.principal, request(bundle, "trace-order"))

    assert result.status == AgentStatus.completed
    assert order.index("commit") < order.index(TraceEventType.tool_called.value)
    assert order.index("commit") < order.index(TraceEventType.agent_completed.value)
