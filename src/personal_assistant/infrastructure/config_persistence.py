"""Persistence configuration loaders from environment."""

from __future__ import annotations

from personal_assistant.infrastructure.config_constants import (
    DEFAULT_DATABASE_SCHEMA,
)
from personal_assistant.infrastructure.config_env import (
    _env,
    _load_env_file,
    _optional_env,
)
from personal_assistant.infrastructure.validation import validate_identifier


def load_persistence_settings_from_env() -> tuple[str, str | None]:
    file_values = _load_env_file()
    return (
        _env("PERSISTENCE_BACKEND", file_values, "memory").strip().lower() or "memory",
        _optional_env("DATABASE_URL", file_values),
    )


def load_database_settings_from_env() -> tuple[str | None, str]:
    """Load only the database settings needed by the migration CLI."""

    file_values = _load_env_file()
    schema = (
        _env("DATABASE_SCHEMA", file_values, DEFAULT_DATABASE_SCHEMA).strip()
        or DEFAULT_DATABASE_SCHEMA
    )
    return (
        _optional_env("DATABASE_URL", file_values),
        validate_identifier(schema, field="schema"),
    )
