"""Propose (never execute) facade partitions for mutation-heavy files.

TEST-007's canonical partition: the original file stays as a facade that
re-exports, each cohesive group of functions migrates to its own submodule,
public imports do not change, tests are untouched. The pilot ran that recipe
by hand three phases in a row (22-24); this command turns it into a plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.wct.config import load_config
from tools.wct.mutate.engine import function_sites

MODULE_BUCKET = "<module>"


def plan(root: Path, target: Path) -> dict[str, Any]:
    """Greedy source-order partition of `target` under the site budget."""
    _root, _policy, thresholds = load_config(root)
    limit = int(thresholds["mutation"]["max_sites_per_file"])
    sites = function_sites(target)
    functions = {name: count for name, count in sites.items() if name != MODULE_BUCKET}
    stem = target.stem
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        relative = str(target)

    oversize = [
        {"function": name, "sites": count}
        for name, count in functions.items()
        if count > limit
    ]
    if oversize:
        detail = ", ".join(
            f"{item['function']} ({item['sites']} sitios)" for item in oversize
        )
        return {
            "file": relative,
            "limit": limit,
            "ok": False,
            "oversize_functions": oversize,
            "message": (
                f"parte la función, no el archivo: {detail} excede el límite de "
                f"{limit} sitios por sí sola; un split de archivo no la reduce"
            ),
        }

    groups: list[list[str]] = []
    current: list[str] = []
    total = 0
    for name, count in functions.items():
        if current and total + count > limit:
            groups.append(current)
            current, total = [], 0
        current.append(name)
        total += count
    if current:
        groups.append(current)

    parts = [
        {
            "module": f"{stem}_part{index}.py",
            "functions": names,
            "sites": sum(functions[name] for name in names),
        }
        for index, names in enumerate(groups, start=1)
    ]
    facade_imports = [
        f"from .{stem}_part{index} import "
        + ", ".join(name.rsplit(".", 1)[-1] for name in names)
        for index, names in enumerate(groups, start=1)
    ]
    return {
        "file": relative,
        "limit": limit,
        "total_sites": sum(sites.values()),
        "module_sites": sites.get(MODULE_BUCKET, 0),
        "ok": True,
        "parts": parts,
        "facade_imports": facade_imports,
        "note": (
            f"los nombres {stem}_partN son mecánicos: renombra cada submódulo "
            "semánticamente; el archivo original queda como fachada que "
            "re-exporta los mismos nombres públicos (TEST-007)"
        ),
    }


def render(report: dict[str, Any]) -> str:
    """Human-readable rendering of a split plan."""
    lines = [f"{report['file']}: presupuesto {report['limit']} sitios por archivo"]
    if not report["ok"]:
        lines.append(report["message"])
        return "\n".join(lines)
    lines.append(
        f"total: {report['total_sites']} sitios ({report['module_sites']} a nivel de módulo)"
    )
    for part in report["parts"]:
        names = ", ".join(part["functions"])
        lines.append(f"- {part['module']} ({part['sites']} sitios): {names}")
    lines.extend(f"fachada: {line}" for line in report["facade_imports"])
    lines.append(f"nota: {report['note']}")
    return "\n".join(lines)
