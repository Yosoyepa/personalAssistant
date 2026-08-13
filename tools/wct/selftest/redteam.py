from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from tools.wct.config import load_yaml
from tools.wct.gate.runner import REGISTRY
from tools.wct.hooks.guard import pre_tool_use
from tools.wct.ratchet.engine import SUPPRESSION

SECRET_PATTERN = re.compile(r"(?:SECRET_ACCESS_KEY|BEGIN PRIVATE KEY)", re.IGNORECASE)
MUTATION_LIMIT = 100
BLOCK_EXIT = 2
CASES_PER_MODE = 2


def _reject(root: Path, checker: str, payload: str) -> bool:
    if checker == "duplicate":
        bodies = re.findall(r"return\s+([^\n]+)", payload)
        return len(bodies) != len(set(bodies))
    if checker == "unused":
        return bool(re.search(r"(?:never_called|generated_helper|UNUSED_)", payload))
    if checker == "testless":
        return "production=true" in payload and "tests=false" in payload
    if checker == "hardcoded":
        return "expected fixture" in payload
    if checker == "introvert":
        return payload.startswith("assert ") and "example." not in payload
    if checker == "mock-only":
        return "assert_called" in payload and "assert " not in payload
    if checker == "suppression":
        return bool(SUPPRESSION.search(payload))
    if checker == "mutation-budget":
        return int(payload) > MUTATION_LIMIT
    if checker == "survivor":
        return int(payload.split("=", 1)[1]) > 0
    if checker == "outer-import":
        layer, source = payload.split(":", 1)
        return layer in {"domain", "application"} and any(
            outer in source for outer in ("adapters", "entrypoints")
        )
    if checker == "cycle":
        edges: list[tuple[str, str]] = []
        for edge in payload.split(","):
            source, target = edge.split("->", 1)
            edges.append((source, target))
        return _contains_cycle(edges)
    if checker == "framework-leak":
        layer, source = payload.split(":", 1)
        return layer in {"domain", "application"} and any(
            framework in source for framework in ("sqlalchemy", "fastapi")
        )
    if checker == "environment":
        layer, source = payload.split(":", 1)
        return layer in {"domain", "application"} and any(
            boundary in source for boundary in ("subprocess", "tkinter")
        )
    if checker == "undeclared":
        return "imaginary_sdk" in payload or "transitive_only" in payload
    if checker == "secret":
        return bool(SECRET_PATTERN.search(payload))
    if checker == "protected-write":
        request = {"tool_name": "Edit", "tool_input": {"file_path": str(root / payload)}}
        return pre_tool_use(root, request) == BLOCK_EXIT
    if checker == "forbidden-command":
        request = {"tool_name": "Bash", "tool_input": {"command": payload}}
        return pre_tool_use(root, request) == BLOCK_EXIT
    return False


def _contains_cycle(edges: list[tuple[str, str]]) -> bool:
    graph: dict[str, set[str]] = {}
    for source, target in edges:
        graph.setdefault(source, set()).add(target)

    def visit(node: str, active: set[str]) -> bool:
        if node in active:
            return True
        return any(visit(child, active | {node}) for child in graph.get(node, set()))

    return any(visit(node, set()) for node in graph)


def run(root: Path) -> tuple[int, list[str]]:
    cases = load_yaml(root / "quality/redteam/cases.yaml").get("cases", [])
    failures: list[str] = []
    counts = Counter(case.get("failure_mode") for case in cases)
    for mode in (f"F{index}" for index in range(1, 16)):
        if counts[mode] < CASES_PER_MODE:
            failures.append(f"{mode}: requiere al menos dos casos")
    for case in cases:
        gate = str(case.get("gate", ""))
        if gate not in REGISTRY:
            failures.append(f"{case.get('id')}: gate inexistente {gate}")
        elif not _reject(root, str(case.get("checker")), str(case.get("payload"))):
            failures.append(f"{case.get('id')}: dejó de ser rechazado por {gate}")
    return len(cases), failures
