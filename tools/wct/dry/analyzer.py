from __future__ import annotations

import ast
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.wct.config import load_config


@dataclass(frozen=True)
class Unit:
    """Normalized function and its structural fingerprints."""

    file: str
    name: str
    start: int
    end: int
    lines: int
    nodes: int
    fingerprints: frozenset[str]
    canonical: str


class Normalizer(ast.NodeTransformer):
    """Erase names and literals while preserving executable structure."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="name", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        return ast.copy_location(
            ast.arg(arg="arg", annotation=self.visit(node.annotation) if node.annotation else None),
            node,
        )

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        return ast.copy_location(
            ast.Attribute(value=self.visit(node.value), attr="attr", ctx=node.ctx), node
        )

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        kind = type(node.value).__name__
        return ast.copy_location(ast.Constant(value=f"<{kind}>"), node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        transformed = self.generic_visit(node)
        if not isinstance(transformed, ast.FunctionDef):
            raise TypeError("FunctionDef normalization changed node type")
        transformed.name = "function"
        return transformed

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        transformed = self.generic_visit(node)
        if not isinstance(transformed, ast.AsyncFunctionDef):
            raise TypeError("AsyncFunctionDef normalization changed node type")
        transformed.name = "function"
        return transformed


def _unit(path: Path, root: Path, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Unit:
    normalized = Normalizer().visit(ast.fix_missing_locations(ast.parse(ast.unparse(node)))).body[0]
    dumps = [
        ast.dump(child, annotate_fields=False, include_attributes=False)
        for child in ast.walk(normalized)
    ]
    fingerprints = frozenset(
        hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest() for value in dumps
    )
    return Unit(
        path.relative_to(root).as_posix(),
        node.name,
        node.lineno,
        node.end_lineno or node.lineno,
        (node.end_lineno or node.lineno) - node.lineno + 1,
        len(dumps),
        fingerprints,
        ast.dump(normalized, annotate_fields=False, include_attributes=False),
    )


def analyze(root: Path, paths: list[Path] | None = None) -> dict[str, Any]:
    _root, policy, thresholds = load_config(root)
    config = thresholds["dry"]
    if paths is None:
        paths = []
        for directory in policy["paths"]["source"]:
            paths.extend((root / directory).rglob("*.py"))
    units: list[Unit] = []
    errors: list[str] = []
    for path in sorted(set(paths)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(root)}:{exc.lineno}: {exc.msg}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                item = _unit(path, root, node)
                if item.lines >= int(config["min_lines"]) and item.nodes >= int(
                    config["min_nodes"]
                ):
                    units.append(item)
    candidates: list[dict[str, Any]] = []
    threshold = float(config["threshold"])
    extraction = config["extraction"]
    for index, left in enumerate(units):
        for right in units[index + 1 :]:
            union = left.fingerprints | right.fingerprints
            score = len(left.fingerprints & right.fingerprints) / len(union) if union else 0.0
            if score < threshold:
                continue
            shared = len(left.fingerprints & right.fingerprints)
            variations = max(0, len(union) - shared)
            intensity = 2
            if shared <= int(extraction["min_shared_forms"]) or variations > int(
                extraction["max_variable_points"]
            ):
                pressure = 0.0
            else:
                before = max(0, shared - 3) * ((intensity - 1) ** 1.5) / (variations + 1)
                after = max(0, shared - 3) / (variations + 2)
                pressure = max(0.0, before - after - float(extraction["helper_cost"]))
            review_threshold = 0.95
            action = (
                "EXTRACT"
                if pressure >= float(extraction["min_pressure"])
                else "REVIEW"
                if score >= review_threshold
                else "LEAVE_ALONE"
            )
            candidates.append(
                {
                    "score": round(score, 4),
                    "left": {
                        key: value
                        for key, value in asdict(left).items()
                        if key not in {"fingerprints", "canonical"}
                    },
                    "right": {
                        key: value
                        for key, value in asdict(right).items()
                        if key not in {"fingerprints", "canonical"}
                    },
                    "shared_fingerprints": shared,
                    "variation_points": variations,
                    "extraction_pressure": round(pressure, 4),
                    "ai_actionability": action,
                }
            )
    return {
        "candidates": sorted(candidates, key=lambda value: value["score"], reverse=True),
        "errors": errors,
        "units": len(units),
    }
