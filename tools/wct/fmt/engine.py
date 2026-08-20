from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tools.wct.util.git import changed_files, run_git, staged_files

RUFF_CONFIG = "governance/lint/ruff.toml"


def _base(root: Path) -> str | None:
    for candidate in ("main", "master"):
        if (
            run_git(root, "rev-parse", "--verify", candidate, check=False).returncode
            == 0
        ):
            return candidate
    return None


def changeset(root: Path, *, staged_only: bool) -> list[Path]:
    """Python files an agent is allowed to format: the changeset, never the tree.

    Formatting legacy files by accident detonates G-MUT-SITES in files owned
    by other tasks.
    """
    selected = staged_files(root) if staged_only else changed_files(root, _base(root))
    return [path for path in selected if path.suffix == ".py"]


def run(root: Path, *, staged_only: bool = False) -> int:
    files = changeset(root, staged_only=staged_only)
    if not files:
        print("sin cambios que formatear")
        return 0
    if shutil.which("ruff") is None:
        print("ruff no está instalado; ejecuta `uv sync --group dev`", file=sys.stderr)
        return 2
    names = [path.relative_to(root).as_posix() for path in files]
    completed = subprocess.run(
        ["ruff", "format", "--config", RUFF_CONFIG, *names],
        cwd=root,
        check=False,
    )
    for name in names:
        print(name)
    return completed.returncode
