"""Validation helpers for PostgreSQL migration identifiers."""

from __future__ import annotations

import re


_POSTGRES_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_POSTGRES_IDENTIFIER_MAX_BYTES = 63


def validate_identifier(value: str, *, field: str = "identifier") -> str:
    """Return a PostgreSQL identifier after strict, ASCII-only validation."""

    if not isinstance(value, str) or not _POSTGRES_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid PostgreSQL {field}: {value!r}")
    if len(value.encode("utf-8")) > _POSTGRES_IDENTIFIER_MAX_BYTES:
        raise ValueError(
            f"invalid PostgreSQL {field}: identifiers are limited to "
            f"{_POSTGRES_IDENTIFIER_MAX_BYTES} bytes"
        )
    return value


def quote_identifier(value: str, *, field: str = "identifier") -> str:
    """Quote an already constrained identifier without unsafe interpolation."""

    return f'"{validate_identifier(value, field=field)}"'
