from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from tools.wct.config import load_config


def _names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def analyze(root: Path, paths: list[Path] | None = None) -> dict[str, Any]:
    _root, policy, _thresholds = load_config(root)
    package = policy["architecture"]["root_package"]
    sut_prefixes = (package, "tools.wct")
    if paths is None:
        paths = [
            path
            for directory in policy["paths"]["tests"]
            for path in (root / directory).rglob("test_*.py")
            if "acceptance" not in path.parts
        ]
    verdicts: list[dict[str, Any]] = []
    for path in sorted(paths):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            verdicts.append(
                {
                    "file": str(path.relative_to(root)),
                    "test": "<parse>",
                    "line": exc.lineno,
                    "verdict": "questionable",
                    "reason": exc.msg,
                }
            )
            continue
        sut_aliases: set[str] = set()
        direct_sut: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == prefix or alias.name.startswith(prefix + ".")
                        for prefix in sut_prefixes
                    ):
                        sut_aliases.add(alias.asname or alias.name.split(".")[0])
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and any(
                    node.module == prefix or node.module.startswith(prefix + ".")
                    for prefix in sut_prefixes
                )
            ):
                direct_sut.update(alias.asname or alias.name for alias in node.names)
        for test in [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test")
        ]:
            derived: set[str] = set()
            calls_sut = False
            assertions: list[ast.Assert] = []
            mock_assertions = 0
            conditional = False
            raises_assertion = False
            for child in ast.walk(test):
                if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None:
                    value_names = _names(child.value)
                    grounded = bool(value_names & (sut_aliases | direct_sut | derived))
                    if grounded:
                        calls_sut = True
                        targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                        for target in targets:
                            derived.update(_names(target))
                elif isinstance(child, ast.Call):
                    call_names = _names(child.func)
                    calls_sut |= bool(call_names & (sut_aliases | direct_sut))
                    name = ast.unparse(child.func)
                    mock_assertions += int("assert_called" in name or "assert_awaited" in name)
                elif isinstance(child, ast.Assert):
                    assertions.append(child)
                    conditional |= any(
                        isinstance(parent, ast.If) and child in list(ast.walk(parent))
                        for parent in ast.walk(test)
                    )
                elif isinstance(child, ast.With):
                    raises_assertion |= any(
                        isinstance(item.context_expr, ast.Call)
                        and ast.unparse(item.context_expr.func).endswith("raises")
                        for item in child.items
                    )
            grounded_asserts = sum(
                bool(_names(assertion.test) & (sut_aliases | direct_sut | derived))
                for assertion in assertions
            )
            if grounded_asserts or (raises_assertion and calls_sut):
                verdict = "conditional-assertion" if conditional else "extroverted"
                reason = "la aserción traza a un valor del SUT"
            elif calls_sut and assertions:
                verdict, reason = "cloistered", "llama al SUT pero asevera datos no derivados"
            elif calls_sut and mock_assertions and not assertions:
                verdict, reason = (
                    "introverted",
                    "solo verifica llamadas al mock; no el resultado del SUT",
                )
            elif assertions:
                verdict, reason = "introverted", "las aserciones no trazan al SUT"
            else:
                verdict, reason = "questionable", "no contiene aserciones reconocibles"
            verdicts.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "test": test.name,
                    "line": test.lineno,
                    "verdict": verdict,
                    "reason": reason,
                }
            )
    counts: dict[str, int] = {}
    for item in verdicts:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    return {"tests": verdicts, "counts": counts}
