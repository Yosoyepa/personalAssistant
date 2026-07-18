"""Cross-process operational signals with deliberately metadata-only payloads."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from personal_assistant.infrastructure.migrations.validation import quote_identifier


WORKER_NAME = "reminder-delivery"
DELIVERY_STATUSES = (
    "pending",
    "claimed",
    "sending",
    "published",
    "failed",
    "uncertain",
)


class WorkerHeartbeatStore(Protocol):
    """Small injectable boundary used by the worker and readiness probe."""

    def record(self, observed_at: datetime) -> None: ...

    def latest(self) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class HeartbeatAssessment:
    status: str
    fresh: bool


def assess_heartbeat(
    observed_at: datetime | None,
    *,
    now: datetime,
    timeout_seconds: float,
) -> HeartbeatAssessment:
    """Assess freshness without exposing the stored timestamp or process identity."""

    canonical_now = _aware_utc(now)
    if observed_at is None:
        return HeartbeatAssessment(status="missing", fresh=False)
    canonical_observed = _aware_utc(observed_at)
    age_seconds = (canonical_now - canonical_observed).total_seconds()
    if age_seconds < 0 or age_seconds > timeout_seconds:
        return HeartbeatAssessment(status="stale", fresh=False)
    return HeartbeatAssessment(status="ok", fresh=True)


class PostgresWorkerHeartbeatStore:
    """Persist one non-identifying worker heartbeat in PostgreSQL."""

    def __init__(
        self,
        *,
        dsn: str,
        schema: str,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not dsn.strip() and connection_factory is None:
            raise ValueError("DATABASE_URL is required for worker heartbeat storage")
        self._dsn = dsn
        self._connection_factory = connection_factory
        self._table = (
            f"{quote_identifier(schema, field='schema')}."
            f"{quote_identifier('assistant_worker_heartbeats', field='table name')}"
        )

    def record(self, observed_at: datetime) -> None:
        heartbeat_at = _aware_utc(observed_at)
        with self._connection() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._table} (worker_name, heartbeat_at)
                VALUES (%s, %s)
                ON CONFLICT (worker_name) DO UPDATE
                SET heartbeat_at = EXCLUDED.heartbeat_at
                """,
                (WORKER_NAME, heartbeat_at),
            )

    def latest(self) -> datetime | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT heartbeat_at FROM {self._table} WHERE worker_name = %s",
                (WORKER_NAME,),
            ).fetchone()
        return None if row is None else _aware_utc(row[0])

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        connection = (
            self._connection_factory()
            if self._connection_factory is not None
            else _load_psycopg().connect(self._dsn, autocommit=True)
        )
        try:
            yield connection
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()


def empty_delivery_counts() -> dict[str, int]:
    return {status: 0 for status in DELIVERY_STATUSES}


def _load_psycopg() -> Any:
    return importlib.import_module("psycopg")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("operational timestamps must be timezone-aware")
    return value.astimezone(UTC)
