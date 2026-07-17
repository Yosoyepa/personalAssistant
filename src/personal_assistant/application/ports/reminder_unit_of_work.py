"""Atomic persistence boundary for reminder creation."""

from __future__ import annotations

from enum import Enum
from types import TracebackType
from typing import Protocol

from personal_assistant.application.ports.calendar import CalendarPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.scheduler import ReminderSchedulerPort
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.domain.common.identity import Principal


class ReminderTransactionConflictKind(str, Enum):
    """Safe PostgreSQL conflict categories."""

    serialization_failure = "serialization_failure"
    deadlock_detected = "deadlock_detected"
    unique_violation = "unique_violation"


class ReminderTransactionConflict(RuntimeError):
    """A sanitized database conflict whose transaction was rolled back."""

    def __init__(self, kind: ReminderTransactionConflictKind) -> None:
        self.kind = kind
        super().__init__(f"reminder transaction conflict: {kind.value}")


class ReminderCommitOutcomeUnknown(RuntimeError):
    """The connection was lost before the result of commit was known.

    Callers must not retry the commit operation itself. They may safely run the
    workflow again with the same idempotency identity to observe either the
    committed replay or a fresh attempt.
    """

    def __init__(self) -> None:
        super().__init__("reminder transaction commit outcome is unknown")


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
