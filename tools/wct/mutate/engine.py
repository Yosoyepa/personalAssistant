from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tools.wct.config import load_config
from tools.wct.integrity.engine import bless

MANIFEST = Path("governance/generated/mutation-manifest.json")
MANIFEST_SCHEMA = 2


def _fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Position-independent AST hash: moving a function keeps its identity."""
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def function_hashes(path: Path, root: Path) -> dict[str, str]:
    """Map `file::qualname` to a semantic fingerprint of each function.

    Identity deliberately excludes lineno: inserting an import above must not
    invalidate every function below it (the main source of G-MUT-SITES
    friction in legacy files). Renaming an enclosing class changes the
    qualname, as does changing the function body.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prefix = path.relative_to(root).as_posix()
    hashes: dict[str, str] = {}

    def visit(node: ast.AST, parts: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = ".".join([*parts, child.name])
                hashes[f"{prefix}::{name}"] = _fingerprint(child)
                visit(child, [*parts, child.name])
            elif isinstance(child, ast.ClassDef):
                visit(child, [*parts, child.name])
            else:
                visit(child, parts)

    visit(tree, [])
    return hashes


MUTABLE_NODES = (
    ast.Compare,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.If,
    ast.IfExp,
    ast.While,
    ast.Constant,
)


def _is_site(node: ast.AST) -> bool:
    return isinstance(node, MUTABLE_NODES) and not (
        isinstance(node, ast.Constant) and node.value in {None, Ellipsis}
    )


def mutation_sites(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(_is_site(node) for node in ast.walk(tree))


def function_sites(path: Path) -> dict[str, int]:
    """Mutation sites per function/method qualname, plus the `<module>` bucket.

    Every site lands in exactly one bucket: the innermost enclosing function
    (classes only contribute to the qualname), or module level. The sum of
    the values equals `mutation_sites(path)` — split plans rely on it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    counts: dict[str, int] = {"<module>": 0}

    def visit(node: ast.AST, prefix: list[str], scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join([*prefix, child.name])
                counts[qualname] = counts.get(qualname, 0)
                visit(child, [*prefix, child.name], qualname)
            elif isinstance(child, ast.ClassDef):
                visit(child, [*prefix, child.name], scope)
            else:
                if _is_site(child):
                    counts[scope] += 1
                visit(child, prefix, scope)

    visit(tree, [], "<module>")
    return counts


def scan(root: Path) -> dict[str, Any]:
    _root, policy, thresholds = load_config(root)
    previous: dict[str, str] = {}
    manifest_status = "missing"
    manifest = root / MANIFEST
    if manifest.is_file():
        document = json.loads(manifest.read_text(encoding="utf-8"))
        # Un manifest legacy (claves con lineno, schema 1) no casa con nada:
        # toda función cuenta como cambiada hasta migrar con update-manifest.
        # Rojo explícito, nunca silencio.
        if int(document.get("schema_version", 0)) >= MANIFEST_SCHEMA:
            previous = document.get("functions", {})
            manifest_status = "ok"
        else:
            manifest_status = "legacy"
    functions: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    limit = int(thresholds["mutation"]["max_sites_per_file"])
    for directory in policy["paths"]["source"]:
        for path in sorted((root / directory).rglob("*.py")):
            current = function_hashes(path, root)
            functions.update(current)
            sites = mutation_sites(path)
            changed = sorted(
                key for key, value in current.items() if previous.get(key) != value
            )
            files.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "sites": sites,
                    "over_limit": sites > limit,
                    "changed_functions": changed,
                }
            )
    return {
        "files": files,
        "functions": functions,
        "changed_functions": sum(len(item["changed_functions"]) for item in files),
        "over_limit": [item["file"] for item in files if item["over_limit"]],
        "manifest": manifest_status,
    }


def update_manifest(root: Path, *, approved_by: str = "", reason: str = "") -> Path:
    """Regenerate the manifest, optionally blessing the lock in the same step.

    G-META-1 never observes a stale lock.

    Passing --approved-by/--reason remains a human-only act: the PreToolUse
    guard blocks agents from supplying them. The improvement is atomicity,
    not permission.
    """
    if bool(approved_by) != bool(reason):
        raise ValueError(
            "update-manifest con bless requiere --approved-by y --reason juntos"
        )
    report = scan(root)
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": MANIFEST_SCHEMA, "functions": report["functions"]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if approved_by and reason:
        bless(root, reason, approved_by)
    return path


def run(root: Path) -> int:
    report = scan(root)
    if report["over_limit"]:
        raise ValueError(
            f"más de 100 mutation sites: {', '.join(report['over_limit'])}"
        )
    if report["changed_functions"] == 0:
        print("No hay funciones cambiadas respecto al manifest.")
        return 0
    if shutil.which("mutmut") is None:
        raise RuntimeError(
            "mutmut no está instalado; ejecuta `uv sync --group quality`"
        )
    return subprocess.run(["mutmut", "run"], cwd=root, check=False).returncode
