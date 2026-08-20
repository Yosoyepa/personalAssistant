from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.wct.config import load_config


@dataclass(frozen=True)
class PackageMetric:
    """Robert Martin package metrics for one Python package."""

    package: str
    fan_in: int
    fan_out: int
    instability: float
    abstractness: float
    distance: float
    zone: str
    symbols: int
    abstract_symbols: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def module_name(source_root: Path, path: Path, package: str) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or package


def _iter_runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield nodes except those under `if TYPE_CHECKING:` blocks.

    Type-only imports are erased at runtime; counting them as dependencies
    turns deliberate cycle-breaking shims (lazy `__getattr__` re-exports)
    into false cycles.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If):
            test = child.test
            if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            ):
                continue
        yield child
        yield from _iter_runtime_nodes(child)


def _imports(tree: ast.AST, current: str) -> set[str]:
    found: set[str] = set()
    for node in _iter_runtime_nodes(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = current.split(".")[: -node.level]
                module = ".".join([*parent, *(node.module or "").split(".")]).strip(".")
            else:
                module = node.module or ""
            if module:
                found.add(module)
    return found


def _dynamic_imports(tree: ast.AST, package: str) -> list[tuple[int, str]]:
    """Find `importlib.import_module`/`__import__` calls that hide edges.

    A dynamic import is invisible to the static graph, so it can evade
    G-ARCH-CYCLE without anyone noticing. Calls naming project modules are
    violations; calls with computed names are flagged as opaque because they
    cannot be proven innocent.
    """
    findings: list[tuple[int, str]] = []
    for node in _iter_runtime_nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        imports_dynamically = (
            isinstance(function, ast.Attribute) and function.attr == "import_module"
        ) or (isinstance(function, ast.Name) and function.id == "__import__")
        if not imports_dynamically:
            continue
        argument = node.args[0] if node.args else None
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            target = argument.value
            if target == package or target.startswith(package + "."):
                findings.append(
                    (node.lineno, f"import dinámico oculta la arista a {target}")
                )
        else:
            findings.append(
                (node.lineno, "import dinámico opaco (argumento no literal)")
            )
    return findings


def _is_abstract(node: ast.AST) -> bool:
    if isinstance(node, ast.ClassDef):
        bases = {ast.unparse(base) for base in node.bases}
        if bases & {"Protocol", "typing.Protocol", "ABC", "abc.ABC"}:
            return True
        return any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                ast.unparse(dec).endswith("abstractmethod")
                for dec in child.decorator_list
            )
            for child in node.body
        )
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return any(
            ast.unparse(dec).endswith("singledispatch") for dec in node.decorator_list
        )
    if isinstance(node, ast.Assign):
        return (
            isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func).endswith("TypeVar")
            and any(kw.arg == "bound" for kw in node.value.keywords)
        )
    return False


def analyze(root: Path) -> dict[str, Any]:
    _root, policy, thresholds = load_config(root)
    package = policy["architecture"]["root_package"]
    source_root = root / policy["paths"]["source"][0]
    package_root = source_root / package
    trees: dict[str, ast.Module] = {}
    files: dict[str, Path] = {}
    parse_errors: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        module = module_name(source_root, path, package)
        try:
            trees[module] = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            files[module] = path
        except SyntaxError as exc:
            parse_errors.append(f"{path.relative_to(root)}:{exc.lineno}: {exc.msg}")

    edges: set[tuple[str, str]] = set()
    external: dict[str, set[str]] = {}
    for module, tree in trees.items():
        for imported in _imports(tree, module):
            if imported == package or imported.startswith(package + "."):
                target = max(
                    (
                        candidate
                        for candidate in trees
                        if imported == candidate or imported.startswith(candidate + ".")
                    ),
                    key=len,
                    default=imported,
                )
                if target != module:
                    edges.add((module, target))
            else:
                external.setdefault(module, set()).add(imported.split(".")[0])

    layers = list(policy["architecture"]["layers"])
    positions = {name: index for index, name in enumerate(layers)}
    violations: list[str] = list(parse_errors)
    for source, target in sorted(edges):
        source_layer = source.split(".")[1] if len(source.split(".")) > 1 else ""
        target_layer = target.split(".")[1] if len(target.split(".")) > 1 else ""
        if (
            source_layer in positions
            and target_layer in positions
            and source_layer != target_layer
            and positions[target_layer] <= positions[source_layer]
        ):
            violations.append(f"dependency rule: {source} -> {target}")
    for module, names in external.items():
        layer = module.split(".")[1] if len(module.split(".")) > 1 else ""
        forbidden = set(
            policy["architecture"].get("forbidden_external", {}).get(layer, [])
        )
        for name in sorted(names & forbidden):
            violations.append(f"external forbidden: {module} -> {name}")

    graph: dict[str, set[str]] = {module: set() for module in trees}
    for source, target in edges:
        graph[source].add(target)
    allowlist = thresholds["architecture"].get("cycle_allowlist") or []
    allowed_sets = [frozenset(entry.get("modules", [])) for entry in allowlist]
    allowed_modules = {module for modules in allowed_sets for module in modules}
    detected = _cycles(graph)
    allowlisted = [cycle for cycle in detected if frozenset(cycle) in allowed_sets]
    cycles = [cycle for cycle in detected if frozenset(cycle) not in allowed_sets]
    violations.extend(f"cycle: {' -> '.join(cycle)}" for cycle in cycles)
    for module, tree in trees.items():
        if module in allowed_modules:
            continue
        for lineno, message in _dynamic_imports(tree, package):
            violations.append(f"dynamic import: {module}:{lineno}: {message}")

    packages = [
        f"{package}.{layer}" for layer in layers if f"{package}.{layer}" in trees
    ]
    threshold = float(thresholds["architecture"]["healthy_threshold"])
    metrics: list[PackageMetric] = []
    for current in packages:
        members = {
            module
            for module in trees
            if module == current or module.startswith(current + ".")
        }
        outgoing = {
            target.rsplit(".", 1)[0]
            for source, target in edges
            if source in members and target not in members
        }
        incoming = {
            source.rsplit(".", 1)[0]
            for source, target in edges
            if target in members and source not in members
        }
        symbols = abstract = 0
        for module in members:
            for node in trees[module].body:
                if isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign),
                ):
                    symbols += 1
                    abstract += int(_is_abstract(node))
        fan_in, fan_out = len(incoming), len(outgoing)
        instability = fan_out / (fan_in + fan_out) if fan_in + fan_out else 0.0
        abstractness = abstract / symbols if symbols else 0.0
        distance = abs(abstractness + instability - 1)
        total = abstractness + instability
        zone = (
            "pain"
            if total < 1 - threshold
            else "useless"
            if total > 1 + threshold
            else "healthy"
        )
        metrics.append(
            PackageMetric(
                current,
                fan_in,
                fan_out,
                instability,
                abstractness,
                distance,
                zone,
                symbols,
                abstract,
            )
        )
    return {
        "metrics": [item.as_dict() for item in metrics],
        "edges": sorted([list(edge) for edge in edges]),
        "cycles": cycles,
        "allowlisted_cycles": allowlisted,
        "violations": violations,
    }


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visiting: list[str] = []
    visited: set[str] = set()
    found: set[tuple[str, ...]] = set()

    def walk(node: str) -> None:
        if node in visiting:
            cycle = [*visiting[visiting.index(node) :], node]
            core = cycle[:-1]
            rotations = [
                tuple(core[index:] + core[:index]) for index in range(len(core))
            ]
            canonical = min(rotations)
            found.add((*canonical, canonical[0]))
            return
        if node in visited:
            return
        visiting.append(node)
        for child in sorted(graph.get(node, set())):
            if child in graph:
                walk(child)
        visiting.pop()
        visited.add(node)

    for module in sorted(graph):
        walk(module)
    return [list(cycle) for cycle in sorted(found)]
