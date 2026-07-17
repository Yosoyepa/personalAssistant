"""Serializable in-memory unit of work for reminder creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType
from typing import Literal

from personal_assistant.adapters._in_memory_transaction import (
    InMemoryTransactionParticipant,
    ReentrantLock,
)
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.application.ports.calendar import CalendarPort
from personal_assistant.application.ports.events import EventStorePort, OutboxPort
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderTransaction,
)
from personal_assistant.application.ports.scheduler import ReminderSchedulerPort
from personal_assistant.application.ports.workflow_state import WorkflowStateStorePort
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)


@dataclass(slots=True)
class InMemoryReminderTransaction:
    """One serializable transaction across the five reminder stores.

    Writes are applied while every participant lock is held. Other readers of
    these adapters therefore observe either the state before the transaction or
    the state after its context exits. A deep snapshot restores all business
    data and idempotency indexes on rollback. Diagnostic traces are deliberately
    outside this boundary.
    """

    principal: Principal
    calendar: CalendarPort
    scheduler: ReminderSchedulerPort
    event_store: EventStorePort
    outbox: OutboxPort
    states: WorkflowStateStorePort
    _participants: tuple[InMemoryTransactionParticipant, ...]
    _locks: list[ReentrantLock] = field(default_factory=list, init=False)
    _snapshots: list[object] = field(default_factory=list, init=False)
    _entered: bool = field(default=False, init=False)
    _committed: bool = field(default=False, init=False)
    _rolled_back: bool = field(default=False, init=False)

    def __enter__(self) -> InMemoryReminderTransaction:
        if self._entered:
            raise RuntimeError("reminder transaction cannot be entered twice")
        self._entered = True
        try:
            # The participant tuple is constructed in the documented fixed
            # order: calendar, scheduler, event store, outbox, workflow state.
            for participant in self._participants:
                lock = participant._reminder_transaction_lock
                lock.acquire()
                self._locks.append(lock)
            self._snapshots = [
                participant._snapshot_reminder_transaction()
                for participant in self._participants
            ]
        except BaseException:
            self._release_locks()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            self._release_locks()
        return False

    def commit(self) -> None:
        self._require_active()
        if self._rolled_back:
            raise RuntimeError("cannot commit a rolled-back reminder transaction")
        self._committed = True

    def rollback(self) -> None:
        self._require_active()
        if self._rolled_back:
            return
        for participant, snapshot in reversed(
            list(zip(self._participants, self._snapshots, strict=True))
        ):
            participant._restore_reminder_transaction(snapshot)
        self._rolled_back = True
        self._committed = False

    def _require_active(self) -> None:
        if not self._entered or not self._locks:
            raise RuntimeError("reminder transaction is not active")

    def _release_locks(self) -> None:
        for lock in reversed(self._locks):
            lock.release()
        self._locks.clear()


@dataclass(frozen=True, slots=True)
class InMemoryReminderUnitOfWork:
    """Creates in-memory reminder transactions with deterministic lock order."""

    calendar: LocalCalendarTool
    scheduler: ReminderScheduler
    event_store: InMemoryEventStore
    outbox: InMemoryOutbox
    states: InMemoryWorkflowStateStore

    def begin(self, principal: Principal) -> ReminderTransaction:
        require_trusted_principal(principal)
        participants: tuple[InMemoryTransactionParticipant, ...] = (
            self.calendar,
            self.scheduler,
            self.event_store,
            self.outbox,
            self.states,
        )
        return InMemoryReminderTransaction(
            principal=principal,
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            _participants=participants,
        )
