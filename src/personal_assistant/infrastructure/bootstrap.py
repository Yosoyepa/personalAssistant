"""Application composition for the local-first assistant MVP."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.application.use_cases.reminder_notifications import DispatchDueReminders
from personal_assistant.application.use_cases.reminders import ReminderWorkflow
from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.persistence.in_memory import InMemoryEventStore, InMemoryOutbox, InMemoryWorkflowStateStore
from personal_assistant.adapters.persistence.memory import TenantMemoryStore
from personal_assistant.adapters.outbound.notifications.local import LocalNotificationTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler


@dataclass(slots=True)
class AppContainer:
    calendar: LocalCalendarTool
    documents: DocumentService
    event_store: InMemoryEventStore
    memory: TenantMemoryStore
    notifications: LocalNotificationTool
    outbox: InMemoryOutbox
    reminder_notifications: DispatchDueReminders
    reminder_workflow: ReminderWorkflow
    scheduler: ReminderScheduler
    states: InMemoryWorkflowStateStore
    traces: TraceRecorder


def build_container() -> AppContainer:
    """Build in-memory adapters for local development and tests."""
    notifications = LocalNotificationTool()
    calendar = LocalCalendarTool()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    scheduler = ReminderScheduler()
    states = InMemoryWorkflowStateStore()
    traces = TraceRecorder()
    return AppContainer(
        calendar=calendar,
        documents=DocumentService(),
        event_store=event_store,
        memory=TenantMemoryStore(),
        notifications=notifications,
        outbox=outbox,
        reminder_notifications=DispatchDueReminders(scheduler=scheduler, notifications=notifications),
        reminder_workflow=ReminderWorkflow(
            calendar=calendar,
            scheduler=scheduler,
            event_store=event_store,
            outbox=outbox,
            states=states,
            traces=traces,
        ),
        scheduler=scheduler,
        states=states,
        traces=traces,
    )
