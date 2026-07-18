"""Shared real-PostgreSQL isolation for reliability eval executors."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
import os
import secrets
from typing import Iterator

from personal_assistant.infrastructure.migrations import apply_migrations


SCHEMA_PREFIX = "eval_reliability_"


class MissingTestPostgresDsnError(RuntimeError):
    """The reliability gate was invoked without its required database."""


@dataclass(frozen=True, slots=True)
class PostgresEvalDatabase:
    dsn: str
    schema: str


def required_test_dsn() -> str:
    dsn = os.environ.get("TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        raise MissingTestPostgresDsnError(
            "TEST_POSTGRES_DSN is required for PostgreSQL reliability evals"
        )
    return dsn


def _safe_schema(schema: str) -> str:
    suffix = schema.removeprefix(SCHEMA_PREFIX)
    if not schema.startswith(SCHEMA_PREFIX) or len(suffix) != 24:
        raise RuntimeError("refusing to operate on a non-eval schema")
    if any(character not in "0123456789abcdef" for character in suffix):
        raise RuntimeError("refusing to operate on a malformed eval schema")
    return schema


@contextmanager
def isolated_postgres() -> Iterator[PostgresEvalDatabase]:
    """Create, migrate, and safely remove one unique schema for one eval case."""

    dsn = required_test_dsn()
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    schema = _safe_schema(f"{SCHEMA_PREFIX}{secrets.token_hex(12)}")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield PostgresEvalDatabase(dsn=dsn, schema=schema)
    finally:
        safe = _safe_schema(schema)
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(safe)
                )
            )


def schema_exists(dsn: str, schema: str) -> bool:
    psycopg = import_module("psycopg")
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
            (schema,),
        ).fetchone()
    return bool(row and row[0])
