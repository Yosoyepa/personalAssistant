"""Unit and PostgreSQL tests for operator-invoked trace retention pruning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
import importlib
import json
import os
import secrets
from typing import Any, Iterator

import pytest

from personal_assistant.infrastructure import trace_retention
from personal_assistant.infrastructure.migrations import apply_migrations


CUTOFF = datetime(2026, 7, 1, 12, tzinfo=UTC)


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.rowcount = 0
        self._result: tuple[object, ...] | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self, statement: str, params: tuple[object, ...] | None = None
    ) -> None:
        self.connection.statements.append((statement, params))
        if self.connection.error is not None:
            raise self.connection.error
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("SELECT NOW()"):
            self._result = (self.connection.cutoff,)
        elif normalized.startswith("SELECT COUNT(*)"):
            self._result = (self.connection.matched,)
        elif normalized.startswith("DELETE"):
            self.rowcount = self.connection.matched

    def fetchone(self) -> tuple[object, ...]:
        if self.connection.fetch_error is not None:
            raise self.connection.fetch_error
        assert self._result is not None
        return self._result


class _Transaction:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection

    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        self.connection.transaction_error = args[0]
        return None


class _Connection:
    autocommit = True

    def __init__(
        self,
        *,
        matched: int = 0,
        cutoff: datetime = CUTOFF,
        error: Exception | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.matched = matched
        self.cutoff = cutoff
        self.error = error
        self.fetch_error = fetch_error
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.transaction_error: object = None

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def transaction(self) -> _Transaction:
        return _Transaction(self)


def test_dry_run_counts_without_deleting() -> None:
    connection = _Connection(matched=7)

    result = trace_retention.prune_postgres_traces(connection=connection)

    assert result.dry_run is True
    assert result.matched == 7
    assert result.deleted == 0
    assert result.cutoff == CUTOFF.isoformat()
    assert result.retention_days == trace_retention.DEFAULT_TRACE_RETENTION_DAYS
    statements = [statement for statement, _ in connection.statements]
    assert not any(
        statement.lstrip().upper().startswith("DELETE") for statement in statements
    )


def test_apply_deletes_with_the_counted_cutoff() -> None:
    connection = _Connection(matched=3)

    result = trace_retention.prune_postgres_traces(connection=connection, apply=True)

    assert result.dry_run is False
    assert result.deleted == 3
    delete = next(
        (statement, params)
        for statement, params in connection.statements
        if statement.lstrip().upper().startswith("DELETE")
    )
    assert "assistant_trace_events" in delete[0]
    assert "created_at < %s" in delete[0]
    assert delete[1] == (CUTOFF,)


@pytest.mark.parametrize("days", [0, -5, 3651, True, "30"])
def test_retention_days_out_of_range_are_rejected(days: object) -> None:
    with pytest.raises(trace_retention.TraceRetentionError):
        trace_retention.prune_postgres_traces(
            connection=_Connection(), retention_days=days  # type: ignore[arg-type]
        )


def test_cli_requires_literal_confirmation_before_loading_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _unexpected_settings_load() -> tuple[str | None, str]:
        raise AssertionError("database settings must not be loaded")

    monkeypatch.setattr(
        trace_retention,
        "load_database_settings_from_env",
        _unexpected_settings_load,
    )

    assert trace_retention.main(["--apply"]) == 2
    assert trace_retention.main(["--apply", "--confirm", "WRONG_TOKEN"]) == 2
    assert trace_retention.main(["--confirm", trace_retention.CONFIRMATION]) == 2
    captured = capsys.readouterr()
    assert trace_retention.CONFIRMATION in captured.err


@pytest.mark.parametrize("days", ["0", "-1", "3651"])
def test_cli_rejects_out_of_range_days_before_loading_database(
    monkeypatch: pytest.MonkeyPatch, days: str
) -> None:
    def _unexpected_settings_load() -> tuple[str | None, str]:
        raise AssertionError("database settings must not be loaded")

    monkeypatch.setattr(
        trace_retention,
        "load_database_settings_from_env",
        _unexpected_settings_load,
    )

    assert trace_retention.main(["--days", days]) == 2


def test_cli_rejects_non_integer_env_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_settings_load() -> tuple[str | None, str]:
        raise AssertionError("database settings must not be loaded")

    monkeypatch.setattr(trace_retention, "_load_env_file", dict)
    monkeypatch.setattr(
        trace_retention,
        "load_database_settings_from_env",
        _unexpected_settings_load,
    )
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "not-an-integer")

    assert trace_retention.main([]) == 2


def test_retention_days_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    file_values = {"TRACE_RETENTION_DAYS": "60"}
    assert trace_retention._resolve_retention_days(45, file_values) == 45
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    assert trace_retention._resolve_retention_days(None, file_values) == 60
    assert trace_retention._resolve_retention_days(None, {}) == 30


def test_transaction_failure_rolls_back_and_stays_generic() -> None:
    connection = _Connection(error=RuntimeError("sensitive driver internals"))

    with pytest.raises(trace_retention.TraceRetentionError) as excinfo:
        trace_retention.prune_postgres_traces(connection=connection)

    assert (
        str(excinfo.value)
        == "trace retention pruning failed; transaction rolled back"
    )
    assert connection.transaction_error is RuntimeError
    assert "sensitive driver internals" not in str(excinfo.value)


def test_retention_error_inside_transaction_is_not_rewrapped() -> None:
    failure = trace_retention.TraceRetentionError("unsafe row shape")
    connection = _Connection(fetch_error=failure)

    with pytest.raises(trace_retention.TraceRetentionError) as excinfo:
        trace_retention.prune_postgres_traces(connection=connection)

    assert excinfo.value is failure
    assert connection.transaction_error is trace_retention.TraceRetentionError


def test_missing_psycopg_reports_the_postgres_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None) -> Any:
        if name == "psycopg":
            raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    with pytest.raises(trace_retention.TraceRetentionError) as excinfo:
        trace_retention._load_psycopg()
    assert "personal-assistant[postgres]" in str(excinfo.value)


def test_unrelated_module_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_import_module(name: str, package: str | None = None) -> Any:
        raise ModuleNotFoundError(
            "No module named 'psycopg_pool'", name="psycopg_pool"
        )

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    with pytest.raises(ModuleNotFoundError):
        trace_retention._load_psycopg()


def test_open_connection_rejects_dsn_and_connection_together() -> None:
    with pytest.raises(
        trace_retention.TraceRetentionError,
        match="provide only one of dsn or connection",
    ):
        trace_retention.prune_postgres_traces(
            dsn="postgresql://example", connection=_Connection()
        )


def test_open_connection_requires_autocommit() -> None:
    connection = _Connection()
    connection.autocommit = False

    with pytest.raises(
        trace_retention.TraceRetentionError, match="autocommit"
    ):
        trace_retention.prune_postgres_traces(connection=connection)


@pytest.mark.parametrize("dsn", [None, "   "])
def test_open_connection_requires_database_url(dsn: str | None) -> None:
    with pytest.raises(
        trace_retention.TraceRetentionError, match="DATABASE_URL is required"
    ):
        trace_retention.prune_postgres_traces(dsn=dsn)


def test_cli_operational_failure_reports_only_the_exception_class(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(trace_retention, "_load_env_file", dict)
    monkeypatch.setattr(
        trace_retention,
        "load_database_settings_from_env",
        lambda: ("postgresql://example", "public"),
    )

    def _unexpected_prune(**_kwargs: object) -> trace_retention.TraceRetentionResult:
        raise RuntimeError("sensitive driver internals")

    monkeypatch.setattr(trace_retention, "prune_postgres_traces", _unexpected_prune)

    assert trace_retention.main([]) == 1
    captured = capsys.readouterr()
    assert (
        captured.err.strip()
        == "trace retention database operation failed (RuntimeError)"
    )
    assert "sensitive driver internals" not in captured.err
    assert captured.out == ""


def test_cli_expected_failure_reports_the_safe_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(trace_retention, "_load_env_file", dict)
    monkeypatch.setattr(
        trace_retention,
        "load_database_settings_from_env",
        lambda: ("postgresql://example", "public"),
    )

    def _refusing_prune(**_kwargs: object) -> trace_retention.TraceRetentionResult:
        raise trace_retention.TraceRetentionError("unsafe row shape")

    monkeypatch.setattr(trace_retention, "prune_postgres_traces", _refusing_prune)

    assert trace_retention.main([]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "trace retention error: unsafe row shape"
    assert captured.out == ""


@dataclass(frozen=True, slots=True)
class PostgresSandbox:
    dsn: str
    schema: str


@pytest.fixture
def retention_postgres() -> Iterator[PostgresSandbox]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL retention tests")
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    schema = f"p6_a3_{secrets.token_hex(6)}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield PostgresSandbox(dsn=dsn, schema=schema)
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def _seed_trace(
    connection: Any,
    sql: Any,
    schema: str,
    trace_id: str,
    created_at: datetime,
) -> None:
    connection.execute(
        sql.SQL(
            """
            INSERT INTO {}.assistant_trace_events (
                tenant_id, trace_id, run_id, agent_id, event_type,
                timestamp, parent_event_id, fingerprint, payload, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """
        ).format(sql.Identifier(schema)),
        (
            "personal",
            trace_id,
            f"run-{trace_id}",
            "personal_assistant",
            "agent.started",
            created_at,
            None,
            secrets.token_hex(32),
            "{}",
            created_at,
        ),
    )


def _seed_retention_rows(sandbox: PostgresSandbox) -> None:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    now = datetime.now(UTC)
    with psycopg.connect(sandbox.dsn, autocommit=True) as connection:
        for index in range(2):
            _seed_trace(
                connection,
                sql,
                sandbox.schema,
                f"old-{index}",
                now - timedelta(days=45),
            )
        for index in range(3):
            _seed_trace(
                connection,
                sql,
                sandbox.schema,
                f"recent-{index}",
                now - timedelta(days=5),
            )


def _trace_rows(sandbox: PostgresSandbox) -> list[str]:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    with psycopg.connect(sandbox.dsn, autocommit=True) as connection:
        rows = connection.execute(
            sql.SQL(
                "SELECT trace_id FROM {}.assistant_trace_events ORDER BY trace_id"
            ).format(sql.Identifier(sandbox.schema))
        ).fetchall()
    return [row[0] for row in rows]


def _table_counts(sandbox: PostgresSandbox) -> dict[str, int]:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    counts: dict[str, int] = {}
    with psycopg.connect(sandbox.dsn, autocommit=True) as connection:
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s",
            (sandbox.schema,),
        ).fetchall()
        for (table_name,) in tables:
            counts[table_name] = connection.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(sandbox.schema),
                    sql.Identifier(table_name),
                )
            ).fetchone()[0]
    return counts


def test_postgres_dry_run_deletes_nothing(
    retention_postgres: PostgresSandbox,
) -> None:
    _seed_retention_rows(retention_postgres)

    result = trace_retention.prune_postgres_traces(
        dsn=retention_postgres.dsn,
        schema=retention_postgres.schema,
        retention_days=30,
    )

    assert result.dry_run is True
    assert result.matched == 2
    assert result.deleted == 0
    assert len(_trace_rows(retention_postgres)) == 5


def test_postgres_apply_deletes_only_old_rows_and_is_idempotent(
    retention_postgres: PostgresSandbox,
) -> None:
    _seed_retention_rows(retention_postgres)
    before = _table_counts(retention_postgres)

    first = trace_retention.prune_postgres_traces(
        dsn=retention_postgres.dsn,
        schema=retention_postgres.schema,
        retention_days=30,
        apply=True,
    )

    assert first.deleted == 2
    assert _trace_rows(retention_postgres) == ["recent-0", "recent-1", "recent-2"]
    after = _table_counts(retention_postgres)
    for table_name, count in after.items():
        deleted = 2 if table_name == "assistant_trace_events" else 0
        assert count == before[table_name] - deleted, (
            f"unexpected change in {table_name}"
        )

    second = trace_retention.prune_postgres_traces(
        dsn=retention_postgres.dsn,
        schema=retention_postgres.schema,
        retention_days=30,
        apply=True,
    )
    assert second.matched == 0
    assert second.deleted == 0
    assert len(_trace_rows(retention_postgres)) == 3


def test_postgres_cli_dry_run_reports_counts_only(
    retention_postgres: PostgresSandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_retention_rows(retention_postgres)
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("DATABASE_URL", retention_postgres.dsn)
    monkeypatch.setenv("DATABASE_SCHEMA", retention_postgres.schema)
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)

    assert trace_retention.main(["--days", "30"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "schema": retention_postgres.schema,
        "retention_days": 30,
        "cutoff": report["cutoff"],
        "dry_run": True,
        "matched": 2,
        "deleted": 0,
    }
    assert len(_trace_rows(retention_postgres)) == 5
