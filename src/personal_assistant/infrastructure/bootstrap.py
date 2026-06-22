"""Application composition for the local-first assistant MVP."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.adapters.persistence.memory import TenantMemoryStore
from personal_assistant.adapters.outbound.notifications.local import LocalNotificationTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler


@dataclass(slots=True)
class AppContainer:
    calendar: LocalCalendarTool
    documents: DocumentService
    memory: TenantMemoryStore
    notifications: LocalNotificationTool
    scheduler: ReminderScheduler


def build_container() -> AppContainer:
    """Build in-memory adapters for local development and tests."""
    notifications = LocalNotificationTool()
    return AppContainer(
        calendar=LocalCalendarTool(),
        documents=DocumentService(),
        memory=TenantMemoryStore(),
        notifications=notifications,
        scheduler=ReminderScheduler(notification_tool=notifications),
    )

