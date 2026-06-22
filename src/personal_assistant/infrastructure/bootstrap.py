"""Application composition for the local-first assistant MVP."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.application.ports.notifications import NotificationPort
from personal_assistant.application.use_cases.commands import ConversationCommandService
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.application.use_cases.reminder_notifications import DispatchDueReminders
from personal_assistant.application.use_cases.reminders import ReminderWorkflow
from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryApprovalStore,
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.memory import TenantMemoryStore
from personal_assistant.adapters.outbound.notifications.local import LocalNotificationTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.infrastructure.worker import ReminderWorker, RuntimeNotificationApprovalPolicy


@dataclass(slots=True)
class AppContainer:
    approvals: InMemoryApprovalStore
    calendar: LocalCalendarTool
    commands: ConversationCommandService
    documents: DocumentService
    event_store: InMemoryEventStore
    memory: TenantMemoryStore
    notifications: NotificationPort
    outbox: InMemoryOutbox
    reminder_notifications: DispatchDueReminders
    reminder_worker: ReminderWorker
    reminder_workflow: ReminderWorkflow
    scheduler: ReminderScheduler
    states: InMemoryWorkflowStateStore
    traces: TraceRecorder


def build_container(
    *,
    notifications: NotificationPort | None = None,
    approve_reminder_notifications: bool = False,
) -> AppContainer:
    """Build in-memory adapters for local development and tests."""
    notification_adapter = notifications or LocalNotificationTool()
    approvals = InMemoryApprovalStore()
    calendar = LocalCalendarTool()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    scheduler = ReminderScheduler()
    states = InMemoryWorkflowStateStore()
    traces = TraceRecorder()
    reminder_notifications = DispatchDueReminders(scheduler=scheduler, notifications=notification_adapter)
    reminder_workflow = ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
    )
    commands = ConversationCommandService(
        approvals=approvals,
        calendar=calendar,
        reminder_workflow=reminder_workflow,
        states=states,
        event_store=event_store,
        outbox=outbox,
    )
    return AppContainer(
        approvals=approvals,
        calendar=calendar,
        commands=commands,
        documents=DocumentService(),
        event_store=event_store,
        memory=TenantMemoryStore(),
        notifications=notification_adapter,
        outbox=outbox,
        reminder_notifications=reminder_notifications,
        reminder_worker=ReminderWorker(
            dispatcher=reminder_notifications,
            approval_policy=RuntimeNotificationApprovalPolicy(
                approve_notifications=approve_reminder_notifications,
            ),
        ),
        reminder_workflow=reminder_workflow,
        scheduler=scheduler,
        states=states,
        traces=traces,
    )
