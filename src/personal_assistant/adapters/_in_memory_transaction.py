"""Internal primitives shared by transactional in-memory adapters."""

from __future__ import annotations

from threading import RLock
from types import TracebackType
from typing import Protocol, cast


class ReentrantLock(Protocol):
    """Small structural type for ``threading.RLock``."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the lock."""

    def release(self) -> None:
        """Release the lock."""

    def __enter__(self) -> bool:
        """Acquire the lock for a context manager."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lock for a context manager."""


class InMemoryTransactionParticipant(Protocol):
    """Store whose state can participate in an in-memory transaction."""

    @property
    def _reminder_transaction_lock(self) -> ReentrantLock:
        """Return the stable lock protecting this store."""

    def _snapshot_reminder_transaction(self) -> object:
        """Return a deep snapshot of persisted data and auxiliary indexes."""

    def _restore_reminder_transaction(self, snapshot: object) -> None:
        """Restore a snapshot without changing non-persistent test controls."""


def new_reentrant_lock() -> ReentrantLock:
    """Create an ``RLock`` behind a structural type understood by mypy."""

    return cast(ReentrantLock, RLock())
