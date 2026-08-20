from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.wct.config import load_config
from tools.wct.util.git import head_sha, tracked_files

LOCK_PATH = Path("governance/integrity.lock")
MIN_REASON = 12
MIN_APPROVER = 2
HASH_ALGORITHM = "sha256:eol-normalized"
# Un --reason en prosa no prueba nada: exigimos citar la aprobación (URL de
# PR/comentario del mantenedor, o referencia #N de PR/issue).
APPROVAL_EVIDENCE = re.compile(r"https?://\S+|#\d+")


def require_approval_evidence(reason: str) -> None:
    """Reject approval reasons that do not cite where the approval happened."""
    if not APPROVAL_EVIDENCE.search(reason):
        raise ValueError(
            "reason debe citar la evidencia de aprobación (URL o #N de PR/issue)"
        )


def _protected(root: Path) -> list[Path]:
    _root, policy, _thresholds = load_config(root)
    patterns = policy["paths"]["protected"]
    files: set[Path] = set()
    ignored_parts = {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or ".git" in path.parts
            or set(path.parts) & ignored_parts
        ):
            continue
        relative = path.relative_to(root).as_posix()
        if relative == LOCK_PATH.as_posix():
            continue
        if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
            files.add(path)
    return sorted(files)


def _digest_eol_normalized(path: Path) -> str:
    """Hash content with git-style EOL semantics.

    CRLF checkouts of the same blob (Linux/Windows) produce the same digest
    and never demand a re-bless.
    """
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _digest_raw(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path, algorithm: str | None) -> dict[str, str]:
    digest = _digest_eol_normalized if algorithm == HASH_ALGORITHM else _digest_raw
    return {
        path.relative_to(root).as_posix(): digest(path) for path in _protected(root)
    }


def snapshot(root: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "hash": HASH_ALGORITHM,
        "commit": head_sha(root),
        "files": _files(root, HASH_ALGORITHM),
    }


def write_lock(root: Path, *, force: bool = False) -> Path:
    path = root / LOCK_PATH
    if path.exists() and not force:
        raise ValueError(
            "integrity.lock ya existe; solo un humano puede actualizarlo con `wct integrity bless`"
        )
    path.write_text(
        json.dumps(snapshot(root), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _classify(
    previous: dict[str, str],
    actual: dict[str, str],
    tracked: set[str] | None,
) -> tuple[list[str], list[str]]:
    """Split lock drift into blocking violations and non-blocking warnings.

    A pull request can only modify git-tracked files, so a protected path that
    is missing from disk but is not versioned (e.g. git-ignored local skills
    under .agents/skills/) cannot be attacked through a PR: it is reported as
    a warning instead of a violation. Without git tracking information the
    check fails closed: every missing protected path remains a violation.
    """
    problems = [
        f"modificado: {name}"
        for name in sorted(actual.keys() & previous.keys())
        if actual[name] != previous[name]
    ]
    problems += [
        f"nuevo protegido: {name}" for name in sorted(actual.keys() - previous.keys())
    ]
    warnings: list[str] = []
    for name in sorted(previous.keys() - actual.keys()):
        if tracked is None or name in tracked:
            problems.append(f"eliminado protegido: {name}")
        else:
            warnings.append(f"ausente no versionado (omitido): {name}")
    return problems, warnings


def review(root: Path) -> tuple[list[str], list[str]]:
    """Return (violations, warnings) comparing integrity.lock with the tree."""
    path = root / LOCK_PATH
    if not path.is_file():
        return [
            f"falta {LOCK_PATH}; ejecuta `wct integrity lock` durante bootstrap"
        ], []
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"lock ilegible: {exc}"], []
    # Locks legacy (sin campo "hash") se comparan con el algoritmo crudo para
    # no falsificar fallos durante la transición; el próximo bless migra.
    declared = expected.get("hash")
    actual = _files(root, declared if isinstance(declared, str) else None)
    previous = expected.get("files", {})
    return _classify(previous, actual, tracked_files(root))


def violations(root: Path) -> list[str]:
    """Return only blocking drift between integrity.lock and the tree."""
    problems, _warnings = review(root)
    return problems


def bless(root: Path, reason: str, approved_by: str) -> Path:
    if len(reason.strip()) < MIN_REASON or len(approved_by.strip()) < MIN_APPROVER:
        raise ValueError("reason (>=12 caracteres) y approved-by son obligatorios")
    require_approval_evidence(reason)
    log = root / "governance/integrity-log.md"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n## {datetime.now(UTC).isoformat()}\n\n"
            f"- Approved by: {approved_by}\n"
            f"- Reason: {reason}\n"
            f"- Commit: {head_sha(root) or 'unborn'}\n"
        )
    return write_lock(root, force=True)
