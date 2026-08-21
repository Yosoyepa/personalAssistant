from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=check
    )


def _materialized(root: Path, names: set[str]) -> list[Path]:
    return sorted(root / name for name in names if (root / name).is_file())


def changed_files(root: Path, base: str | None = None) -> list[Path]:
    names: set[str] = set()
    commands: list[tuple[str, ...]] = [
        ("diff", "--name-only", "--diff-filter=ACMR", "--cached"),
        ("diff", "--name-only", "--diff-filter=ACMR"),
        ("ls-files", "--others", "--exclude-standard"),
    ]
    if base:
        commands.insert(
            0, ("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
        )
    for command in commands:
        result = run_git(root, *command, check=False)
        if result.returncode == 0:
            names.update(line for line in result.stdout.splitlines() if line)
    return _materialized(root, names)


def staged_files(root: Path) -> list[Path]:
    result = run_git(
        root, "diff", "--name-only", "--diff-filter=ACMR", "--cached", check=False
    )
    if result.returncode != 0:
        return []
    return _materialized(root, {line for line in result.stdout.splitlines() if line})


def head_sha(root: Path) -> str | None:
    result = run_git(root, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def tracked_files(root: Path) -> set[str] | None:
    """Return git-tracked paths relative to root, or None if git is unavailable."""
    result = run_git(root, "ls-files", check=False)
    if result.returncode != 0:
        return None
    return {line for line in result.stdout.splitlines() if line}


def remote_base(root: Path) -> str | None:
    """Remote ref that differential gates compare against (CI parity).

    Order: the base ref GitHub reports for PRs, then the remote default
    branch (what CI diffs against), then the local branch as last resort.
    """
    candidates: list[str] = []
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        candidates.append(f"origin/{base_ref}")
    candidates.extend(["origin/main", "origin/master", "main", "master"])
    for ref in candidates:
        if run_git(root, "rev-parse", "--verify", ref, check=False).returncode == 0:
            return ref
    return None
