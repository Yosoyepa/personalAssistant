from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised when governance configuration cannot be loaded."""

    pass


def find_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("WCT_PROJECT_ROOT")
    current = Path(env_root) if env_root else (start or Path.cwd())
    current = current.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "governance" / "policy.yaml").is_file():
            return candidate
    raise ConfigError("no se encontró governance/policy.yaml en este directorio ni sus padres")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"no se pudo leer {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{path} debe contener un mapa YAML")
    return value


def load_config(root: Path | None = None) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    project = find_root(root)
    policy = load_yaml(project / "governance" / "policy.yaml")
    thresholds = load_yaml(project / "governance" / "thresholds.yaml")
    if policy.get("schema_version") != 1 or thresholds.get("schema_version") != 1:
        raise ConfigError("schema_version no soportado; se esperaba 1")
    return project, policy, thresholds
