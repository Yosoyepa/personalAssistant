"""Versioned, checksum-verified PostgreSQL schema migrations."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib
from pathlib import Path
import re
from typing import Any

from personal_assistant.infrastructure.migrations.validation import (
    quote_identifier,
    validate_identifier,
)


HISTORY_TABLE = "assistant_schema_migrations"
DEFAULT_SCHEMA = "public"
DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).with_name("sql")

_MIGRATION_FILENAME_RE = re.compile(
    r"^(?P<version>[0-9]{4})_(?P<name>[a-z][a-z0-9_]*)\.sql$"
)
_DOLLAR_QUOTE_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_TRANSACTION_COMMANDS = frozenset(
    {"ABORT", "BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT"}
)


class MigrationError(RuntimeError):
    """Base class for safe-to-report migration failures."""


class MigrationConfigurationError(MigrationError):
    """The migration runner was configured incorrectly."""


class MigrationDefinitionError(MigrationError):
    """A migration file or sequence is invalid."""


class MigrationHistoryError(MigrationError):
    """Persisted migration history is incompatible with local files."""


class MigrationChecksumError(MigrationHistoryError):
    """An applied migration no longer has its original SHA-256 checksum."""


class MigrationExecutionError(MigrationError):
    """A migration failed and its transaction was rolled back."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str
    filename: str

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    checksum: str
    applied_at: datetime

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    schema: str
    history_exists: bool
    applied: tuple[AppliedMigration, ...]
    pending: tuple[Migration, ...]

    @property
    def ready(self) -> bool:
        return not self.pending


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    schema: str
    applied: tuple[Migration, ...]
    status: MigrationStatus


def discover_migrations(
    directory: str | Path | None = None,
) -> tuple[Migration, ...]:
    """Load and validate a contiguous migration sequence starting at 0001."""

    root = Path(directory) if directory is not None else DEFAULT_MIGRATIONS_DIRECTORY
    if not root.is_dir():
        raise MigrationDefinitionError(f"migration directory does not exist: {root}")

    migrations: list[Migration] = []
    for path in sorted(root.glob("*.sql")):
        match = _MIGRATION_FILENAME_RE.fullmatch(path.name)
        if match is None:
            raise MigrationDefinitionError(
                f"invalid migration filename {path.name!r}; expected NNNN_name.sql"
            )
        version = int(match.group("version"))
        name = validate_identifier(match.group("name"), field="migration name")
        raw_sql = path.read_bytes()
        try:
            sql = raw_sql.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationDefinitionError(
                f"migration {path.name!r} must be UTF-8"
            ) from exc
        if not sql.strip():
            raise MigrationDefinitionError(f"migration {path.name!r} is empty")
        if _contains_transaction_control(sql):
            raise MigrationDefinitionError(
                f"migration {path.name!r} contains transaction control; "
                "transactions are owned by the runner"
            )
        migrations.append(
            Migration(
                version=version,
                name=name,
                checksum=hashlib.sha256(raw_sql).hexdigest(),
                sql=sql,
                filename=path.name,
            )
        )

    if not migrations:
        raise MigrationDefinitionError(f"no migration files found in {root}")
    versions = [migration.version for migration in migrations]
    expected = list(range(1, len(migrations) + 1))
    if versions != expected:
        raise MigrationDefinitionError(
            f"migration versions must be contiguous from 0001; found {versions}"
        )
    return tuple(migrations)


def _contains_transaction_control(sql: str) -> bool:
    for prefix in _statement_prefixes(sql):
        if prefix[0] in _TRANSACTION_COMMANDS:
            return True
        if len(prefix) < 2:
            continue
        if prefix[:2] in {
            ("PREPARE", "TRANSACTION"),
            ("RELEASE", "SAVEPOINT"),
            ("START", "TRANSACTION"),
        }:
            return True
    return False


def _statement_prefixes(sql: str) -> Iterator[tuple[str, ...]]:
    """Yield the first two unquoted tokens of each top-level SQL statement."""

    prefix: list[str] = []
    position = 0
    while position < len(sql):
        if sql.startswith("--", position):
            newline = sql.find("\n", position + 2)
            position = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", position):
            position = _after_block_comment(sql, position + 2)
            continue

        character = sql[position]
        if character == "'":
            position = _after_quoted_value(sql, position + 1, "'")
            continue
        if character == '"':
            position = _after_quoted_value(sql, position + 1, '"')
            continue
        if character == "$":
            match = _DOLLAR_QUOTE_RE.match(sql, position)
            if match is not None:
                delimiter = match.group(0)
                end = sql.find(delimiter, match.end())
                position = len(sql) if end < 0 else end + len(delimiter)
                continue
        if character == ";":
            if prefix:
                yield tuple(prefix)
            prefix = []
            position += 1
            continue
        if character.isascii() and (character.isalpha() or character == "_"):
            end = position + 1
            while end < len(sql):
                token_character = sql[end]
                if not token_character.isascii() or not (
                    token_character.isalnum() or token_character == "_"
                ):
                    break
                end += 1
            if len(prefix) < 2:
                prefix.append(sql[position:end].upper())
            position = end
            continue
        position += 1

    if prefix:
        yield tuple(prefix)


def _after_quoted_value(sql: str, position: int, delimiter: str) -> int:
    while position < len(sql):
        if sql[position] != delimiter:
            position += 1
            continue
        if position + 1 < len(sql) and sql[position + 1] == delimiter:
            position += 2
            continue
        return position + 1
    return len(sql)


def _after_block_comment(sql: str, position: int) -> int:
    depth = 1
    while position < len(sql) and depth:
        if sql.startswith("/*", position):
            depth += 1
            position += 2
        elif sql.startswith("*/", position):
            depth -= 1
            position += 2
        else:
            position += 1
    return position


def migration_status(
    *,
    dsn: str | None = None,
    schema: str = DEFAULT_SCHEMA,
    connection: Any | None = None,
    migrations_directory: str | Path | None = None,
) -> MigrationStatus:
    """Inspect migration state without creating schemas, tables, or rows."""

    validated_schema = validate_identifier(schema, field="schema")
    migrations = discover_migrations(migrations_directory)
    with _open_connection(dsn=dsn, connection=connection) as active_connection:
        history_exists = _history_exists(active_connection, validated_schema)
        applied = (
            _read_applied(active_connection, validated_schema) if history_exists else ()
        )
    return _build_status(
        schema=validated_schema,
        history_exists=history_exists,
        migrations=migrations,
        applied=applied,
    )


def apply_migrations(
    *,
    dsn: str | None = None,
    schema: str = DEFAULT_SCHEMA,
    connection: Any | None = None,
    migrations_directory: str | Path | None = None,
) -> MigrationApplyResult:
    """Apply every pending migration under a schema-scoped advisory lock."""

    validated_schema = validate_identifier(schema, field="schema")
    migrations = discover_migrations(migrations_directory)
    with _open_connection(dsn=dsn, connection=connection) as active_connection:
        with _advisory_lock(active_connection, validated_schema):
            _ensure_history(active_connection, validated_schema)
            before = _build_status(
                schema=validated_schema,
                history_exists=True,
                migrations=migrations,
                applied=_read_applied(active_connection, validated_schema),
            )
            applied_now: list[Migration] = []
            for migration in before.pending:
                _apply_one(active_connection, validated_schema, migration)
                applied_now.append(migration)
            after = _build_status(
                schema=validated_schema,
                history_exists=True,
                migrations=migrations,
                applied=_read_applied(active_connection, validated_schema),
            )
    return MigrationApplyResult(
        schema=validated_schema,
        applied=tuple(applied_now),
        status=after,
    )


def migration_lock_name(schema: str) -> str:
    """Return the stable advisory-lock namespace used for one schema."""

    return f"personal_assistant:schema_migrations:{validate_identifier(schema, field='schema')}"


def _build_status(
    *,
    schema: str,
    history_exists: bool,
    migrations: Sequence[Migration],
    applied: Sequence[AppliedMigration],
) -> MigrationStatus:
    local_by_version = {migration.version: migration for migration in migrations}
    applied_by_version: dict[int, AppliedMigration] = {}
    for record in applied:
        if record.version in applied_by_version:
            raise MigrationHistoryError(
                f"migration history contains duplicate version {record.version:04d}"
            )
        migration = local_by_version.get(record.version)
        if migration is None:
            raise MigrationHistoryError(
                f"database has unknown applied migration {record.version:04d}_{record.name}"
            )
        if record.name != migration.name:
            raise MigrationHistoryError(
                f"applied migration {record.version:04d} name changed from "
                f"{record.name!r} to {migration.name!r}"
            )
        if record.checksum != migration.checksum:
            raise MigrationChecksumError(
                f"checksum mismatch for applied migration {migration.label}: "
                f"database={record.checksum}, local={migration.checksum}"
            )
        applied_by_version[record.version] = record

    pending: list[Migration] = []
    found_gap = False
    for migration in migrations:
        if migration.version in applied_by_version:
            if found_gap:
                raise MigrationHistoryError(
                    f"migration history has a gap before {migration.label}"
                )
        else:
            found_gap = True
            pending.append(migration)

    ordered_applied = tuple(
        applied_by_version[migration.version]
        for migration in migrations
        if migration.version in applied_by_version
    )
    return MigrationStatus(
        schema=schema,
        history_exists=history_exists,
        applied=ordered_applied,
        pending=tuple(pending),
    )


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg":
            raise MigrationConfigurationError(
                "psycopg is required for PostgreSQL migrations; install the "
                "personal-assistant[postgres] extra"
            ) from exc
        raise


@contextmanager
def _open_connection(*, dsn: str | None, connection: Any | None) -> Iterator[Any]:
    if connection is not None and dsn is not None:
        raise MigrationConfigurationError("provide only one of dsn or connection")
    if connection is not None:
        if getattr(connection, "autocommit", True) is not True:
            raise MigrationConfigurationError(
                "provided migration connection must have autocommit enabled"
            )
        yield connection
        return
    if dsn is None or not dsn.strip():
        raise MigrationConfigurationError("DATABASE_URL is required for migrations")

    psycopg = _load_psycopg()
    active_connection = psycopg.connect(dsn, autocommit=True)
    try:
        yield active_connection
    finally:
        active_connection.close()


@contextmanager
def _cursor(connection: Any) -> Iterator[Any]:
    cursor_context = connection.cursor()
    if hasattr(cursor_context, "__enter__"):
        with cursor_context as cursor:
            yield cursor
        return
    try:
        yield cursor_context
    finally:
        close = getattr(cursor_context, "close", None)
        if callable(close):
            close()


@contextmanager
def _transaction(connection: Any) -> Iterator[None]:
    transaction = getattr(connection, "transaction", None)
    if not callable(transaction):
        raise MigrationConfigurationError(
            "migration connection must support explicit transaction contexts"
        )
    with transaction():
        yield


def _execute(
    connection: Any,
    statement: str,
    params: tuple[object, ...] | None = None,
) -> None:
    with _cursor(connection) as cursor:
        if params is None:
            cursor.execute(statement)
        else:
            cursor.execute(statement, params)


def _fetchone(
    connection: Any,
    statement: str,
    params: tuple[object, ...] | None = None,
) -> Any | None:
    with _cursor(connection) as cursor:
        if params is None:
            cursor.execute(statement)
        else:
            cursor.execute(statement, params)
        return cursor.fetchone()


def _fetchall(
    connection: Any,
    statement: str,
    params: tuple[object, ...] | None = None,
) -> list[Any]:
    with _cursor(connection) as cursor:
        if params is None:
            cursor.execute(statement)
        else:
            cursor.execute(statement, params)
        return list(cursor.fetchall())


def _history_exists(connection: Any, schema: str) -> bool:
    row = _fetchone(
        connection,
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        )
        """,
        (schema, HISTORY_TABLE),
    )
    return bool(row is not None and row[0])


def _read_applied(connection: Any, schema: str) -> tuple[AppliedMigration, ...]:
    history = _qualified_history_table(schema)
    rows = _fetchall(
        connection,
        f"SELECT version, name, checksum, applied_at FROM {history} ORDER BY version",
    )
    return tuple(
        AppliedMigration(
            version=int(row[0]),
            name=str(row[1]),
            checksum=str(row[2]),
            applied_at=row[3],
        )
        for row in rows
    )


def _ensure_history(connection: Any, schema: str) -> None:
    quoted_schema = quote_identifier(schema, field="schema")
    history = _qualified_history_table(schema)
    with _transaction(connection):
        _execute(connection, f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
        _execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS {history} (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name VARCHAR(63) NOT NULL,
                checksum CHAR(64) NOT NULL
                    CHECK (checksum ~ '^[0-9a-f]{{64}}$'),
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )


@contextmanager
def _advisory_lock(connection: Any, schema: str) -> Iterator[None]:
    lock_name = migration_lock_name(schema)
    _execute(
        connection,
        "SELECT pg_catalog.pg_advisory_lock(pg_catalog.hashtextextended(%s, 0))",
        (lock_name,),
    )
    try:
        yield
    finally:
        row = _fetchone(
            connection,
            "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtextextended(%s, 0))",
            (lock_name,),
        )
        if row is not None and not bool(row[0]):
            raise MigrationError("migration advisory lock was not held at release")


def _apply_one(connection: Any, schema: str, migration: Migration) -> None:
    history = _qualified_history_table(schema)
    search_path = quote_identifier(schema, field="schema")
    try:
        with _transaction(connection):
            _execute(
                connection,
                f"SET LOCAL search_path TO {search_path}, pg_catalog",
            )
            _execute(connection, migration.sql)
            _execute(
                connection,
                f"INSERT INTO {history} (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
    except Exception as exc:
        raise MigrationExecutionError(
            f"migration {migration.label} failed; its transaction was rolled back"
        ) from exc


def _qualified_history_table(schema: str) -> str:
    return (
        f"{quote_identifier(schema, field='schema')}."
        f"{quote_identifier(HISTORY_TABLE, field='table name')}"
    )
