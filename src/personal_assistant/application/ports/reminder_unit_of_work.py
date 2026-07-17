"""Atomic persistence boundary for reminder creation."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from personal_assistant.application.ports.calendar import CalendarPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.scheduler import ReminderSchedulerPort
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.domain.common.identity import Principal


class ReminderTransaction(Protocol):
    """Tenant-bound stores participating in one reminder transaction.

    A transaction must roll back when its context exits with an exception or
    without an explicit :meth:`commit`. Implementations must include workflow
    registration/resume state in the same atomic unit as the reminder effects.
    """

    calendar: CalendarPort
    scheduler: ReminderSchedulerPort
    event_store: EventStorePort
    outbox: OutboxPort
    states: WorkflowStateStorePort

    def __enter__(self) -> ReminderTransaction:
        """Open the transaction and return its tenant-bound stores."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit only when requested explicitly; otherwise roll back."""

    def commit(self) -> None:
        """Commit every staged reminder write as one unit."""

    def rollback(self) -> None:
        """Restore the exact state observed when the transaction opened."""


class ReminderUnitOfWork(Protocol):
    """Creates tenant-scoped transactions for the reminder workflow."""

    def begin(self, principal: Principal) -> ReminderTransaction:
        """Return a new transaction bound to the authenticated principal."""
