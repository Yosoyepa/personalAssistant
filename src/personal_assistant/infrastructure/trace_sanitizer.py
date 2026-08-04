"""Explicit, non-destructive sanitization of historical PostgreSQL traces."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.infrastructure.config import load_database_settings_from_env
from personal_assistant.infrastructure.migrations.validation import (
    quote_identifier,
    validate_identifier,
)

CONFIRMATION = "SANITIZE_TRACES"


class TraceSanitizationError(RuntimeError):
    """A safe-to-report historical trace sanitization failure."""


@dataclass(frozen=True, slots=True)
class TraceSanitizationResult:
    schema: str
    scanned: int
    changed: int
    updated: int
    dry_run: bool


def sanitize_postgres_traces(
    *,
    dsn: str | None = None,
    schema: str = "public",
    apply: bool = False,
    connection: Any | None = None,
) -> TraceSanitizationResult:
    """Sanitize every historical trace, using one all-or-nothing transaction.

    Dry-run is the default. The operation rewrites rows in place and never
    deletes them. It deliberately reuses the current application privacy
    boundary instead of duplicating that policy in PostgreSQL SQL functions.
    """

    validated_schema = validate_identifier(schema, field="schema")
    table = (
        f"{quote_identifier(validated_schema, field='schema')}."
        f"{quote_identifier('assistant_trace_events', field='table name')}"
    )
    with _open_connection(dsn=dsn, connection=connection) as active_connection:
        try:
            with active_connection.transaction():
                rows = _read_rows(active_connection, table, lock=apply)
                changes: list[tuple[dict[str, Any], tuple[Any, ...]]] = []
                for position, row in enumerate(rows, start=1):
                    changes.append(_sanitize_row(row, position=position))

                changed_rows = [
                    (safe, identity)
                    for safe, identity in changes
                    if safe["_changed"]
                ]
                if apply:
                    for safe, identity in changed_rows:
                        _update_row(active_connection, table, safe, identity)
        except TraceSanitizationError:
            raise
        except Exception as exc:
            raise TraceSanitizationError(
                "trace sanitization failed; transaction rolled back"
            ) from exc

    return TraceSanitizationResult(
        schema=validated_schema,
        scanned=len(rows),
        changed=len(changed_rows),
        updated=len(changed_rows) if apply else 0,
        dry_run=not apply,
    )


def _read_rows(connection: Any, table: str, *, lock: bool) -> list[Any]:
    lock_clause = " FOR UPDATE" if lock else ""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT tenant_id, trace_id, run_id, agent_id, event_type,
                   timestamp, parent_event_id, fingerprint, payload
            FROM {table}
            ORDER BY tenant_id, trace_id{lock_clause}
            """
        )
        return list(cursor.fetchall())


def _sanitize_row(
    row: Any, *, position: int
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    values = tuple(row)
    if len(values) != 9:
        raise TraceSanitizationError(
            f"trace row {position} has an unexpected storage shape"
        )
    (
        tenant_id,
        trace_id,
        run_id,
        agent_id,
        event_type,
        timestamp,
        parent_event_id,
        fingerprint,
        raw_payload,
    ) = values
    try:
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )
        if not isinstance(payload, dict):
            raise TypeError("payload is not an object")
        safe_event = TraceEvent.model_validate(payload).for_persistence()
    except Exception as exc:
        raise TraceSanitizationError(
            f"trace row {position} cannot be sanitized safely"
        ) from exc

    # Primary-key changes need a separate, relationship-aware migration. Refuse
    # them here rather than silently breaking tenant or trace references.
    if safe_event.tenant_id != str(tenant_id) or safe_event.trace_id != str(trace_id):
        raise TraceSanitizationError(
            f"trace row {position} requires a primary-identifier migration"
        )

    safe_payload = safe_event.model_dump(mode="json")
    serialized = _canonical_json(safe_payload)
    safe_fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    comparable = (
        safe_event.run_id,
        safe_event.agent_id,
        safe_event.event_type.value,
        safe_event.timestamp,
        safe_event.parent_event_id,
        safe_fingerprint,
        serialized,
    )
    current = (
        str(run_id),
        str(agent_id),
        str(event_type),
        timestamp,
        None if parent_event_id is None else str(parent_event_id),
        str(fingerprint),
        _canonical_json(payload),
    )
    return (
        {
            "run_id": comparable[0],
            "agent_id": comparable[1],
            "event_type": comparable[2],
            "timestamp": comparable[3],
            "parent_event_id": comparable[4],
            "fingerprint": comparable[5],
            "payload": serialized,
            "_changed": comparable != current,
        },
        (tenant_id, trace_id),
    )


def _update_row(
    connection: Any,
    table: str,
    safe: dict[str, Any],
    identity: tuple[Any, ...],
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table}
            SET run_id = %s,
                agent_id = %s,
                event_type = %s,
                timestamp = %s,
                parent_event_id = %s,
                fingerprint = %s,
                payload = %s::jsonb
            WHERE tenant_id = %s AND trace_id = %s
            """,
            (
                safe["run_id"],
                safe["agent_id"],
                safe["event_type"],
                safe["timestamp"],
                safe["parent_event_id"],
                safe["fingerprint"],
                safe["payload"],
                *identity,
            ),
        )
        if cursor.rowcount != 1:
            raise TraceSanitizationError(
                "a trace row changed concurrently; transaction rolled back"
            )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg":
            raise TraceSanitizationError(
                "psycopg is required; install the personal-assistant[postgres] extra"
            ) from exc
        raise


@contextmanager
def _open_connection(*, dsn: str | None, connection: Any | None) -> Iterator[Any]:
    if connection is not None and dsn is not None:
        raise TraceSanitizationError("provide only one of dsn or connection")
    if connection is not None:
        if getattr(connection, "autocommit", True) is not True:
            raise TraceSanitizationError(
                "provided connection must have autocommit enabled"
            )
        yield connection
        return
    if dsn is None or not dsn.strip():
        raise TraceSanitizationError("DATABASE_URL is required")

    psycopg = _load_psycopg()
    active_connection = psycopg.connect(dsn, autocommit=True)
    try:
        yield active_connection
    finally:
        active_connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sanitize historical PostgreSQL traces without deleting rows."
    )
    parser.add_argument(
        "--schema",
        help="PostgreSQL schema (defaults to DATABASE_SCHEMA or public)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite changed rows; omit for a read-only dry-run",
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
            f"trace sanitization error: --apply requires --confirm {CONFIRMATION}",
            file=sys.stderr,
        )
        return 2
    if not args.apply and args.confirm is not None:
        print(
            "trace sanitization error: --confirm is valid only with --apply",
            file=sys.stderr,
        )
        return 2

    database_url, configured_schema = load_database_settings_from_env()
    schema = args.schema or configured_schema
    try:
        result = sanitize_postgres_traces(
            dsn=database_url,
            schema=schema,
            apply=args.apply,
        )
    except (TraceSanitizationError, ValueError) as exc:
        print(f"trace sanitization error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"trace sanitization database operation failed "
            f"({exc.__class__.__name__})",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "schema": result.schema,
                "dry_run": result.dry_run,
                "scanned": result.scanned,
                "changed": result.changed,
                "updated": result.updated,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
