"""Explicit, operator-invoked retention pruning of PostgreSQL traces."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from personal_assistant.infrastructure.config import (
    DEFAULT_TRACE_RETENTION_DAYS,
    _env,
    _load_env_file,
    load_database_settings_from_env,
)
from personal_assistant.infrastructure.migrations.validation import (
    quote_identifier,
    validate_identifier,
)

CONFIRMATION = "PRUNE_TRACES"
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650


class TraceRetentionError(RuntimeError):
    """A safe-to-report trace retention pruning failure."""


@dataclass(frozen=True, slots=True)
class TraceRetentionResult:
    schema: str
    retention_days: int
    cutoff: str
    matched: int
    deleted: int
    dry_run: bool


def prune_postgres_traces(
    *,
    dsn: str | None = None,
    schema: str = "public",
    retention_days: int = DEFAULT_TRACE_RETENTION_DAYS,
    apply: bool = False,
    connection: Any | None = None,
) -> TraceRetentionResult:
    """Delete trace rows older than the retention window in one transaction.

    Dry-run is the default and only counts the rows that would be deleted. The
    cutoff is computed once inside the transaction so the count and the delete
    always agree. Output is limited to the cutoff timestamp and row counts; it
    never includes tenant, trace, or user identifiers.
    """

    days = _validate_retention_days(retention_days)
    validated_schema = validate_identifier(schema, field="schema")
    table = (
        f"{quote_identifier(validated_schema, field='schema')}."
        f"{quote_identifier('assistant_trace_events', field='table name')}"
    )
    with _open_connection(dsn=dsn, connection=connection) as active_connection:
        try:
            with (
                active_connection.transaction(),
                active_connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT now() - make_interval(days => %s)", (days,)
                )
                cutoff = cursor.fetchone()[0]
                cursor.execute(
                    f"SELECT count(*) FROM {table} WHERE created_at < %s",
                    (cutoff,),
                )
                matched = int(cursor.fetchone()[0])
                deleted = 0
                if apply:
                    cursor.execute(
                        f"DELETE FROM {table} WHERE created_at < %s",
                        (cutoff,),
                    )
                    deleted = cursor.rowcount
        except TraceRetentionError:
            raise
        except Exception as exc:
            raise TraceRetentionError(
                "trace retention pruning failed; transaction rolled back"
            ) from exc

    return TraceRetentionResult(
        schema=validated_schema,
        retention_days=days,
        cutoff=cutoff.isoformat(),
        matched=matched,
        deleted=deleted,
        dry_run=not apply,
    )


def _validate_retention_days(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < MIN_RETENTION_DAYS
        or value > MAX_RETENTION_DAYS
    ):
        raise TraceRetentionError(
            f"retention days must be an integer in "
            f"{MIN_RETENTION_DAYS}..{MAX_RETENTION_DAYS}"
        )
    return value


def _resolve_retention_days(
    cli_days: int | None, file_values: dict[str, str]
) -> int:
    if cli_days is not None:
        return cli_days
    configured = _env("TRACE_RETENTION_DAYS", file_values).strip()
    if not configured:
        return DEFAULT_TRACE_RETENTION_DAYS
    try:
        return int(configured)
    except ValueError as exc:
        raise TraceRetentionError(
            "TRACE_RETENTION_DAYS must be an integer"
        ) from exc


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg":
            raise TraceRetentionError(
                "psycopg is required; install the personal-assistant[postgres] extra"
            ) from exc
        raise


@contextmanager
def _open_connection(*, dsn: str | None, connection: Any | None) -> Iterator[Any]:
    if connection is not None and dsn is not None:
        raise TraceRetentionError("provide only one of dsn or connection")
    if connection is not None:
        if getattr(connection, "autocommit", True) is not True:
            raise TraceRetentionError(
                "provided connection must have autocommit enabled"
            )
        yield connection
        return
    if dsn is None or not dsn.strip():
        raise TraceRetentionError("DATABASE_URL is required")

    psycopg = _load_psycopg()
    active_connection = psycopg.connect(dsn, autocommit=True)
    try:
        yield active_connection
    finally:
        active_connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete PostgreSQL traces older than the retention window."
    )
    parser.add_argument(
        "--schema",
        help="PostgreSQL schema (defaults to DATABASE_SCHEMA or public)",
    )
    parser.add_argument(
        "--days",
        type=int,
        help=(
            f"retention window in days (defaults to TRACE_RETENTION_DAYS or "
            f"{DEFAULT_TRACE_RETENTION_DAYS}); allowed range "
            f"{MIN_RETENTION_DAYS}-{MAX_RETENTION_DAYS}"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete rows older than the cutoff; omit for a read-only dry-run",
    )
    parser.add_argument(
        "--confirm",
        help=f"required with --apply; exact value: {CONFIRMATION}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply and args.confirm != CONFIRMATION:
        print(
            f"trace retention error: --apply requires --confirm {CONFIRMATION}",
            file=sys.stderr,
        )
        return 2
    if not args.apply and args.confirm is not None:
        print(
            "trace retention error: --confirm is valid only with --apply",
            file=sys.stderr,
        )
        return 2
    try:
        days = _validate_retention_days(
            _resolve_retention_days(args.days, _load_env_file())
        )
    except TraceRetentionError as exc:
        print(f"trace retention error: {exc}", file=sys.stderr)
        return 2

    database_url, configured_schema = load_database_settings_from_env()
    schema = args.schema or configured_schema
    try:
        result = prune_postgres_traces(
            dsn=database_url,
            schema=schema,
            retention_days=days,
            apply=args.apply,
        )
    except (TraceRetentionError, ValueError) as exc:
        print(f"trace retention error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"trace retention database operation failed "
            f"({exc.__class__.__name__})",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "schema": result.schema,
                "retention_days": result.retention_days,
                "cutoff": result.cutoff,
                "dry_run": result.dry_run,
                "matched": result.matched,
                "deleted": result.deleted,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
