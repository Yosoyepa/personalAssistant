"""Application composition for the local-first assistant MVP."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.calendar.local import LocalCalendarTool
from personal_assistant.documents.service import DocumentService
from personal_assistant.memory.store import TenantMemoryStore
from personal_assistant.notifications.local import LocalNotificationTool
from personal_assistant.scheduler.service import ReminderScheduler


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

