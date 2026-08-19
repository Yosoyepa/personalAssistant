from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=check)


def changed_files(root: Path, base: str | None = None) -> list[Path]:
    names: set[str] = set()
    commands: list[tuple[str, ...]] = [
        ("diff", "--name-only", "--diff-filter=ACMR", "--cached"),
        ("diff", "--name-only", "--diff-filter=ACMR"),
        ("ls-files", "--others", "--exclude-standard"),
    ]
    if base:
        commands.insert(0, ("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"))
    for command in commands:
        result = run_git(root, *command, check=False)
        if result.returncode == 0:
            names.update(line for line in result.stdout.splitlines() if line)
    return sorted(root / name for name in names if (root / name).is_file())


def head_sha(root: Path) -> str | None:
    result = run_git(root, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def tracked_files(root: Path) -> set[str] | None:
    """Return git-tracked paths relative to root, or None if git is unavailable."""
    result = run_git(root, "ls-files", check=False)
    if result.returncode != 0:
        return None
    return {line for line in result.stdout.splitlines() if line}
