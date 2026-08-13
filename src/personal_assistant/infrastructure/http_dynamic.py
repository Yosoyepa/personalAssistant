"""Dynamic attribute resolution helper for test-compatibility patches."""

from __future__ import annotations

import sys
from typing import Any


def get_http_attribute(name: str, default: Any) -> Any:
    """Retrieve an attribute from the top-level http facade if patched in tests."""
    http_mod = sys.modules.get("personal_assistant.infrastructure.http")
    if http_mod is not None:
        val = getattr(http_mod, name, None)
        if val is not None:
            return val
    return default
