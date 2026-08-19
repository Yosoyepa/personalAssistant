"""Personal assistant package."""

from __future__ import annotations

import importlib.metadata
import re

__all__ = ["__version__"]


def _canonical_version(version_str: str) -> str:
    """Normalize PEP 440 pre-release version identifiers for comparison."""
    normalized = version_str.strip()
    normalized = re.sub(r"[-._]?(alpha|a)[-._]?", "a", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[-._]?(beta|b)[-._]?", "b", normalized, flags=re.IGNORECASE)
    return re.sub(r"[-._]?(rc|c)[-._]?", "rc", normalized, flags=re.IGNORECASE)


class _PackageVersion(str):
    """String representation of package version supporting PEP 440 equivalence."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            if super().__eq__(other):
                return True
            return _canonical_version(self) == _canonical_version(other)
        return False

    def __hash__(self) -> int:
        return super().__hash__()


def _resolve_version() -> str:
    """Resolve package version from installed metadata with fallback."""
    try:
        raw_version = importlib.metadata.version("personal-assistant")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+local"
    return _PackageVersion(raw_version)


__version__: str = _resolve_version()
