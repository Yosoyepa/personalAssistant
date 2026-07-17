"""Explicit PostgreSQL migration API."""

from personal_assistant.infrastructure.migrations.runner import (
    AppliedMigration,
    Migration,
    MigrationApplyResult,
    MigrationChecksumError,
    MigrationConfigurationError,
    MigrationDefinitionError,
    MigrationError,
    MigrationExecutionError,
    MigrationHistoryError,
    MigrationStatus,
    apply_migrations,
    discover_migrations,
    migration_lock_name,
    migration_status,
)
from personal_assistant.infrastructure.migrations.validation import validate_identifier


__all__ = [
    "AppliedMigration",
    "Migration",
    "MigrationApplyResult",
    "MigrationChecksumError",
    "MigrationConfigurationError",
    "MigrationDefinitionError",
    "MigrationError",
    "MigrationExecutionError",
    "MigrationHistoryError",
    "MigrationStatus",
    "apply_migrations",
    "discover_migrations",
    "migration_lock_name",
    "migration_status",
    "validate_identifier",
]
