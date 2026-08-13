"""Backward-compatible re-exports; the implementation lives in
`personal_assistant.infrastructure.validation` so that `config.py` does not
depend on the migrations package.
"""

from __future__ import annotations

from personal_assistant.infrastructure.validation import (
    quote_identifier,
    validate_identifier,
)

__all__ = ["quote_identifier", "validate_identifier"]
