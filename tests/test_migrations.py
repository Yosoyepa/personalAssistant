from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from personal_assistant.adapters.persistence import postgres
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import create_app
from personal_assistant.infrastructure.migrations import (
    MigrationChecksumError,
    MigrationConfigurationError,
    MigrationDefinitionError,
    MigrationExecutionError,
    MigrationHistoryError,
    apply_migrations,
    discover_migrations,
    migration_lock_name,
    migration_status,
)
from personal_assistant.infrastructure.migrations.__main__ import main as migration_main


TEST_POSTGRES_DSN_ENV = "TEST_POSTGRES_DSN"


def _schema() -> str:
    return f"p3_a2_{uuid4().hex[:20]}"


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.getenv(TEST_POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_POSTGRES_DSN_ENV} is not configured")
    return dsn


@pytest.fixture
def isolated_schema(postgres_dsn: str) -> str:
    psycopg = pytest.importorskip("psycopg")
    schema = _schema()
    try:
        yield schema
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    psycopg.sql.Identifier(schema)
                )
            )


def test_discovers_versioned_migrations_with_sha256_checksums() -> None:
    migrations = discover_migrations()

    assert [migration.label for migration in migrations] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]
    assert all(len(migration.checksum) == 64 for migration in migrations)
    assert [migration.filename for migration in migrations] == [
        "0001_initial.sql",
        "0002_reminder_identity_constraints.sql",
    ]


def test_discovery_rejects_gaps_and_embedded_transactions(tmp_path: Path) -> None:
    (tmp_path / "0001_bad-name.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(MigrationDefinitionError, match="invalid migration filename"):
        discover_migrations(tmp_path)

    (tmp_path / "0001_bad-name.sql").unlink()
    (tmp_path / "0002_gap.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(MigrationDefinitionError, match="contiguous"):
        discover_migrations(tmp_path)

    (tmp_path / "0002_gap.sql").unlink()
    (tmp_path / "0001_bad.sql").write_text(
        "SELECT 1; COMMIT;\n",
        encoding="utf-8",
    )
    with pytest.raises(MigrationDefinitionError, match="transaction control"):
        discover_migrations(tmp_path)

    (tmp_path / "0001_bad.sql").write_text(
        "DO $$\nBEGIN\n    PERFORM 1;\nEND\n$$;\n",
        encoding="utf-8",
    )
    assert discover_migrations(tmp_path)[0].label == "0001_bad"


def test_discovery_rejects_missing_empty_non_utf8_and_blank_sources(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(MigrationDefinitionError, match="directory does not exist"):
        discover_migrations(missing)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(MigrationDefinitionError, match="no migration files"):
        discover_migrations(empty)

    invalid_utf8 = tmp_path / "invalid_utf8"
    invalid_utf8.mkdir()
    (invalid_utf8 / "0001_invalid.sql").write_bytes(b"SELECT '\xff';")
    with pytest.raises(MigrationDefinitionError, match="must be UTF-8"):
        discover_migrations(invalid_utf8)

    blank = tmp_path / "blank"
    blank.mkdir()
    (blank / "0001_blank.sql").write_text("  \n", encoding="utf-8")
    with pytest.raises(MigrationDefinitionError, match="is empty"):
        discover_migrations(blank)


@pytest.mark.parametrize(
    "transaction_sql",
    [
        "PREPARE TRANSACTION 'migration';",
        "RELEASE SAVEPOINT migration_savepoint;",
        "START TRANSACTION;",
    ],
)
def test_discovery_rejects_multiword_transaction_control(
    tmp_path: Path, transaction_sql: str
) -> None:
    (tmp_path / "0001_forbidden.sql").write_text(transaction_sql, encoding="utf-8")

    with pytest.raises(MigrationDefinitionError, match="transaction control"):
        discover_migrations(tmp_path)


def test_discovery_ignores_transaction_words_inside_sql_literals_and_comments(
    tmp_path: Path,
) -> None:
    (tmp_path / "0001_quoted.sql").write_text(
        """
        -- COMMIT
        /* outer ROLLBACK /* nested BEGIN */ still a comment */
        CREATE TABLE transaction_words (
            "COMMIT" TEXT DEFAULT 'ROLLBACK''S',
            body TEXT DEFAULT $tag$START TRANSACTION$tag$
        );
        """,
        encoding="utf-8",
    )

    [migration] = discover_migrations(tmp_path)
    assert migration.label == "0001_quoted"


def test_malicious_schema_is_rejected_before_connecting() -> None:
    with pytest.raises(ValueError, match="schema"):
        migration_status(
            dsn="postgresql://invalid.invalid/unused",
            schema='safe"; DROP SCHEMA public; --',
        )


def test_connection_configuration_is_rejected_before_migration_io(
    postgres_dsn: str,
    isolated_schema: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        with pytest.raises(MigrationConfigurationError, match="only one"):
            migration_status(
                dsn=postgres_dsn,
                connection=connection,
                schema=isolated_schema,
            )
        status = migration_status(connection=connection, schema=isolated_schema)
        assert not status.history_exists

    with psycopg.connect(postgres_dsn) as transactional_connection:
        with pytest.raises(MigrationConfigurationError, match="autocommit"):
            migration_status(
                connection=transactional_connection,
                schema=isolated_schema,
            )

    with pytest.raises(MigrationConfigurationError, match="DATABASE_URL"):
        migration_status(schema=isolated_schema)


def test_migration_cli_status_apply_and_safe_failures(
    postgres_dsn: str,
    isolated_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("DATABASE_SCHEMA", isolated_schema)

    assert migration_main(["status"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["schema"] == isolated_schema
    assert not before["ready"]
    assert [migration["version"] for migration in before["pending"]] == [1, 2]

    assert migration_main(["apply", "--schema", isolated_schema]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["ready"]
    assert applied["applied_now"] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]
    assert [record["version"] for record in applied["applied"]] == [1, 2]
    assert all(record["applied_at"] for record in applied["applied"])

    assert migration_main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == []

    assert migration_main(["status", "--schema", "unsafe-name"]) == 1
    invalid_schema = capsys.readouterr()
    assert invalid_schema.out == ""
    assert "migration error:" in invalid_schema.err
    assert "schema" in invalid_schema.err

    monkeypatch.delenv("DATABASE_URL")
    assert migration_main(["status", "--schema", isolated_schema]) == 1
    missing_database = capsys.readouterr()
    assert missing_database.out == ""
    assert "DATABASE_URL is required" in missing_database.err

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://127.0.0.1:1/unavailable?connect_timeout=1",
    )
    assert migration_main(["status", "--schema", isolated_schema]) == 1
    unavailable = capsys.readouterr()
    assert unavailable.out == ""
    assert unavailable.err.startswith("migration database operation failed (")
    assert unavailable.err.rstrip().endswith(")")
    assert "127.0.0.1" not in unavailable.err


def test_status_apply_and_repeated_apply_are_auditable_no_op(
    postgres_dsn: str,
    isolated_schema: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")

    before = migration_status(dsn=postgres_dsn, schema=isolated_schema)
    assert not before.history_exists
    assert [migration.label for migration in before.pending] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        schema_exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = %s)",
            (isolated_schema,),
        ).fetchone()
    assert schema_exists == (False,)

    first = apply_migrations(dsn=postgres_dsn, schema=isolated_schema)
    second = apply_migrations(dsn=postgres_dsn, schema=isolated_schema)

    assert [migration.label for migration in first.applied] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]
    assert second.applied == ()
    assert second.status.ready
    assert [record.checksum for record in second.status.applied] == [
        migration.checksum for migration in discover_migrations()
    ]


def test_applied_checksum_change_is_an_error(
    postgres_dsn: str,
    isolated_schema: str,
    tmp_path: Path,
) -> None:
    apply_migrations(dsn=postgres_dsn, schema=isolated_schema)
    migrations = discover_migrations()
    source_directory = (
        Path(__file__).parents[1]
        / "src"
        / "personal_assistant"
        / "infrastructure"
        / "migrations"
        / "sql"
    )
    for migration in migrations:
        (tmp_path / migration.filename).write_bytes(
            (source_directory / migration.filename).read_bytes()
        )
    changed = tmp_path / migrations[0].filename
    changed.write_bytes(changed.read_bytes() + b"\n-- changed after application\n")

    with pytest.raises(MigrationChecksumError, match="checksum mismatch"):
        migration_status(
            dsn=postgres_dsn,
            schema=isolated_schema,
            migrations_directory=tmp_path,
        )


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("name", "name changed"),
        ("unknown_version", "unknown applied migration"),
        ("gap", "history has a gap"),
    ],
)
def test_real_history_corruption_is_rejected(
    postgres_dsn: str,
    isolated_schema: str,
    corruption: str,
    message: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    apply_migrations(dsn=postgres_dsn, schema=isolated_schema)
    history = psycopg.sql.SQL("{}.assistant_schema_migrations").format(
        psycopg.sql.Identifier(isolated_schema)
    )
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        if corruption == "name":
            connection.execute(
                psycopg.sql.SQL(
                    "UPDATE {} SET name = 'renamed' WHERE version = 1"
                ).format(history)
            )
        elif corruption == "unknown_version":
            connection.execute(
                psycopg.sql.SQL("UPDATE {} SET version = 3 WHERE version = 2").format(
                    history
                )
            )
        else:
            connection.execute(
                psycopg.sql.SQL("DELETE FROM {} WHERE version = 1").format(history)
            )

    with pytest.raises(MigrationHistoryError, match=message):
        migration_status(dsn=postgres_dsn, schema=isolated_schema)


def test_advisory_lock_serializes_apply(
    postgres_dsn: str,
    isolated_schema: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    lock_name = migration_lock_name(isolated_schema)
    with psycopg.connect(postgres_dsn, autocommit=True) as blocker:
        blocker.execute(
            "SELECT pg_catalog.pg_advisory_lock(pg_catalog.hashtextextended(%s, 0))",
            (lock_name,),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                apply_migrations,
                dsn=postgres_dsn,
                schema=isolated_schema,
            )
            was_blocked = False
            try:
                future.result(timeout=0.25)
            except FutureTimeoutError:
                was_blocked = True
            finally:
                blocker.execute(
                    "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtextextended(%s, 0))",
                    (lock_name,),
                )
            result = future.result(timeout=10)

    assert was_blocked
    assert [migration.label for migration in result.applied] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]


def test_failed_migration_rolls_back_ddl_and_history_version(
    postgres_dsn: str,
    isolated_schema: str,
    tmp_path: Path,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    (tmp_path / "0001_failure_probe.sql").write_text(
        "CREATE TABLE assistant_transaction_probe (id INTEGER);\n"
        "INSERT INTO assistant_transaction_probe (id) VALUES (1);\n"
        "SELECT 1 / 0;\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationExecutionError, match="rolled back"):
        apply_migrations(
            dsn=postgres_dsn,
            schema=isolated_schema,
            migrations_directory=tmp_path,
        )

    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        history_count = connection.execute(
            psycopg.sql.SQL(
                "SELECT COUNT(*) FROM {}.assistant_schema_migrations"
            ).format(psycopg.sql.Identifier(isolated_schema))
        ).fetchone()
        probe_exists = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = 'assistant_transaction_probe'
            )
            """,
            (isolated_schema,),
        ).fetchone()
    assert history_count == (0,)
    assert probe_exists == (False,)


def test_existing_alpha_rows_are_adopted_without_data_loss(
    postgres_dsn: str,
    isolated_schema: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(
                psycopg.sql.Identifier(isolated_schema)
            )
        )
        connection.execute(
            psycopg.sql.SQL(
                """
                CREATE TABLE {}.assistant_workflow_states (
                    tenant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (tenant_id, idempotency_key),
                    UNIQUE (tenant_id, workflow_id)
                )
                """
            ).format(psycopg.sql.Identifier(isolated_schema))
        )
        connection.execute(
            psycopg.sql.SQL(
                """
                INSERT INTO {}.assistant_workflow_states (
                    tenant_id, idempotency_key, workflow_id, workflow_type,
                    status, step, created_at, updated_at, fingerprint, payload
                ) VALUES (
                    'tenant-alpha', 'idem-alpha', 'workflow-alpha', 'reminder',
                    'completed', 'done', now(), now(), %s, %s::jsonb
                )
                """
            ).format(psycopg.sql.Identifier(isolated_schema)),
            ("a" * 64, '{"kept": true}'),
        )

    result = apply_migrations(dsn=postgres_dsn, schema=isolated_schema)

    assert [migration.label for migration in result.applied] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        row = connection.execute(
            psycopg.sql.SQL(
                "SELECT payload_fingerprint, payload->>'kept' "
                "FROM {}.assistant_workflow_states "
                "WHERE tenant_id = 'tenant-alpha'"
            ).format(psycopg.sql.Identifier(isolated_schema))
        ).fetchone()
    assert row == (None, "true")


def test_postgres_startup_has_no_ddl_and_readiness_reports_pending(
    postgres_dsn: str,
    isolated_schema: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    persistence = postgres.build_postgres_persistence(
        database_url=postgres_dsn,
        schema=isolated_schema,
    )
    assert persistence.event_store is not None
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        schema_exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = %s)",
            (isolated_schema,),
        ).fetchone()
    assert schema_exists == (False,)

    settings = AppSettings(
        persistence_backend="postgres",
        database_url=postgres_dsn,
        database_schema=isolated_schema,
    )
    client = TestClient(create_app(container=build_container(), settings=settings))

    assert client.get("/healthz").status_code == 200
    pending = client.get("/readyz")
    assert pending.status_code == 503
    assert pending.json()["status"] == "not_ready"
    assert pending.json()["checks"]["migrations"] == "pending"
    assert pending.json()["pending_migrations"] == [
        "0001_initial",
        "0002_reminder_identity_constraints",
    ]

    apply_migrations(dsn=postgres_dsn, schema=isolated_schema)
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"]["migrations"] == "ok"


@pytest.mark.parametrize(
    ("corruption", "expected_detail"),
    [
        ("checksum", "applied migration checksum mismatch"),
        ("name", "migration history is incompatible with this release"),
    ],
)
def test_readiness_sanitizes_real_migration_history_corruption(
    postgres_dsn: str,
    isolated_schema: str,
    corruption: str,
    expected_detail: str,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    apply_migrations(dsn=postgres_dsn, schema=isolated_schema)
    column = "checksum" if corruption == "checksum" else "name"
    value = "0" * 64 if corruption == "checksum" else "renamed"
    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL(
                "UPDATE {}.assistant_schema_migrations SET {} = %s WHERE version = 1"
            ).format(
                psycopg.sql.Identifier(isolated_schema),
                psycopg.sql.Identifier(column),
            ),
            (value,),
        )

    settings = AppSettings(
        persistence_backend="postgres",
        database_url=postgres_dsn,
        database_schema=isolated_schema,
    )
    response = TestClient(
        create_app(container=build_container(), settings=settings)
    ).get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["migrations"] == "error"
    assert response.json()["detail"] == expected_detail
    assert value not in response.text


@pytest.mark.parametrize(
    ("database_url", "expected_detail"),
    [
        (None, "migration status could not be read"),
        (
            "postgresql://127.0.0.1:1/unavailable?connect_timeout=1",
            "database unavailable or migration status could not be read",
        ),
    ],
)
def test_readiness_sanitizes_configuration_and_database_failures(
    isolated_schema: str,
    database_url: str | None,
    expected_detail: str,
) -> None:
    settings = AppSettings(
        persistence_backend="postgres",
        database_url=database_url,
        database_schema=isolated_schema,
    )

    response = TestClient(
        create_app(container=build_container(), settings=settings)
    ).get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["migrations"] == "error"
    assert response.json()["detail"] == expected_detail
    assert "127.0.0.1" not in response.text
