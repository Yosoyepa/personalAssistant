"""Command-line entry point for explicit PostgreSQL migrations."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from personal_assistant.infrastructure.config import load_database_settings_from_env
from personal_assistant.infrastructure.migrations.runner import (
    MigrationApplyResult,
    MigrationError,
    MigrationStatus,
    apply_migrations,
    migration_status,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m personal_assistant.infrastructure.migrations",
        description="Inspect or apply personal-assistant PostgreSQL migrations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "apply"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--schema",
            help="PostgreSQL schema (defaults to DATABASE_SCHEMA or public)",
        )
    return parser


def _status_payload(status: MigrationStatus) -> dict[str, object]:
    return {
        "schema": status.schema,
        "ready": status.ready,
        "history_exists": status.history_exists,
        "applied": [
            {
                "version": record.version,
                "name": record.name,
                "checksum": record.checksum,
                "applied_at": record.applied_at.isoformat(),
            }
            for record in status.applied
        ],
        "pending": [
            {
                "version": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
            }
            for migration in status.pending
        ],
    }


def _apply_payload(result: MigrationApplyResult) -> dict[str, object]:
    payload = _status_payload(result.status)
    payload["applied_now"] = [migration.label for migration in result.applied]
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_url, configured_schema = load_database_settings_from_env()
    schema = args.schema or configured_schema
    try:
        if args.command == "status":
            payload = _status_payload(migration_status(dsn=database_url, schema=schema))
        else:
            payload = _apply_payload(apply_migrations(dsn=database_url, schema=schema))
    except (MigrationError, ValueError) as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"migration database operation failed ({exc.__class__.__name__})",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
