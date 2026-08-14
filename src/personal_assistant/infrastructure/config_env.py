"""Environment parsing helpers for loading application settings."""

from __future__ import annotations

import os
from math import isfinite
from pathlib import Path

from personal_assistant.domain.common.permissions import PermissionTier


def _load_env_file() -> dict[str, str]:
    configured = os.getenv("APP_ENV_FILE")
    if configured is not None:
        env_path = configured.strip()
        if env_path.lower() in {"", "disabled", "none"}:
            return {}
    else:
        env_path = ".env"
    path = Path(env_path)
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _env(name: str, file_values: dict[str, str], default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        value = file_values.get(name)
    if value is None:
        return default
    return value


def _optional_env(name: str, file_values: dict[str, str]) -> str | None:
    value = _env(name, file_values)
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_bool(name: str, file_values: dict[str, str], default: bool = False) -> bool:
    value = _env(name, file_values, "true" if default else "false").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _finite_seconds(name: str, value: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _env_permission_tier(
    name: str,
    file_values: dict[str, str],
    default: PermissionTier,
) -> PermissionTier:
    configured = _env(name, file_values, default.value).strip().upper()
    try:
        return PermissionTier(configured)
    except ValueError as exc:
        raise ValueError(f"{name} must be one of P0-P6") from exc


def _parse_csv(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())
