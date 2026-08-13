from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tools.wct.config import load_config

MANIFEST = Path("governance/generated/mutation-manifest.json")


def function_hashes(path: Path, root: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hashes: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            key = f"{path.relative_to(root).as_posix()}::{node.name}:{node.lineno}"
            hashes[key] = hashlib.sha256(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest()
    return hashes


def mutation_sites(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mutable = (
        ast.Compare,
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.If,
        ast.IfExp,
        ast.While,
        ast.Constant,
    )
    return sum(
        isinstance(node, mutable)
        and not (isinstance(node, ast.Constant) and node.value in {None, Ellipsis})
        for node in ast.walk(tree)
    )


def scan(root: Path) -> dict[str, Any]:
    _root, policy, thresholds = load_config(root)
    previous: dict[str, str] = {}
    manifest = root / MANIFEST
    if manifest.is_file():
        previous = json.loads(manifest.read_text(encoding="utf-8")).get("functions", {})
    functions: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    limit = int(thresholds["mutation"]["max_sites_per_file"])
    for directory in policy["paths"]["source"]:
        for path in sorted((root / directory).rglob("*.py")):
            current = function_hashes(path, root)
            functions.update(current)
            sites = mutation_sites(path)
            changed = sorted(key for key, value in current.items() if previous.get(key) != value)
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
    }


def update_manifest(root: Path) -> Path:
    report = scan(root)
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": 1, "functions": report["functions"]}, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def run(root: Path) -> int:
    report = scan(root)
    if report["over_limit"]:
        raise ValueError(f"más de 100 mutation sites: {', '.join(report['over_limit'])}")
    if report["changed_functions"] == 0:
        print("No hay funciones cambiadas respecto al manifest.")
        return 0
    if shutil.which("mutmut") is None:
        raise RuntimeError("mutmut no está instalado; ejecuta `uv sync --group quality`")
    return subprocess.run(["mutmut", "run"], cwd=root, check=False).returncode
