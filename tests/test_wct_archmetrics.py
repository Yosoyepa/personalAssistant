"""Tests for WCT architectural metrics and dynamic import detection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from tools.wct.archmetrics.analyzer import analyze


def _make_layers(package: Path) -> None:
    for layer in ("domain", "application", "adapters", "infrastructure", "evals"):
        (package / layer).mkdir(parents=True, exist_ok=True)
        (package / layer / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")


def _allow_cycle(root: Path, modules: list[str]) -> None:
    thresholds = root / "governance/thresholds.yaml"
    document = yaml.safe_load(thresholds.read_text(encoding="utf-8"))
    document["architecture"]["cycle_allowlist"] = [
        {"modules": modules, "reason": "wiring diferido documentado"}
    ]
    thresholds.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_dependency_rule_rejects_domain_importing_adapter(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text(
        "from example.adapters import repository\n", encoding="utf-8"
    )
    (package / "adapters/repository.py").write_text("value = 1\n", encoding="utf-8")

    report = analyze(root)

    assert any("dependency rule" in item for item in report["violations"])


def test_clean_inward_dependency_has_no_violation(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text("class Model: pass\n", encoding="utf-8")
    (package / "application/use_case.py").write_text(
        "from example.domain.model import Model\n", encoding="utf-8"
    )

    assert analyze(root)["violations"] == []


def test_runtime_cycle_between_siblings_is_flagged(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/alpha.py").write_text(
        "import example.domain.beta\n", encoding="utf-8"
    )
    (package / "domain/beta.py").write_text(
        "import example.domain.alpha\n", encoding="utf-8"
    )

    report = analyze(root)

    assert any(item.startswith("cycle:") for item in report["violations"])


def test_type_checking_only_cycle_is_not_an_edge(
    project_factory: Callable[..., Path],
) -> None:
    """TYPE_CHECKING imports are erased at runtime."""
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    shim = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import example.domain.beta\n"
        "def __getattr__(name):\n"
        "    raise AttributeError(name)\n"
    )
    (package / "domain/alpha.py").write_text(shim, encoding="utf-8")
    (package / "domain/beta.py").write_text(
        "import example.domain.alpha\n", encoding="utf-8"
    )

    report = analyze(root)

    assert not any(item.startswith("cycle:") for item in report["violations"])


def test_allowlisted_cycle_is_not_a_violation(
    project_factory: Callable[..., Path],
) -> None:
    """Documented deferred wiring can be exempted in thresholds.yaml."""
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/alpha.py").write_text(
        "import example.domain.beta\n", encoding="utf-8"
    )
    (package / "domain/beta.py").write_text(
        "import example.domain.alpha\n", encoding="utf-8"
    )
    _allow_cycle(root, ["example.domain.alpha", "example.domain.beta"])

    report = analyze(root)

    assert not any(item.startswith("cycle:") for item in report["violations"])
    assert report["cycles"] == []
    assert len(report["allowlisted_cycles"]) == 1


def test_importlib_dynamic_import_of_project_module_is_flagged(
    project_factory: Callable[..., Path],
) -> None:
    """`importlib.import_module` hides edges from static analysis."""
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text(
        "import importlib\n"
        "def load():\n"
        "    return importlib.import_module('example.adapters.repo')\n",
        encoding="utf-8",
    )

    report = analyze(root)

    assert any("import dinámico" in item for item in report["violations"])


def test_dunder_import_of_project_module_is_flagged(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text(
        "def load():\n    return __import__('example.adapters.repo')\n",
        encoding="utf-8",
    )

    report = analyze(root)

    assert any("import dinámico" in item for item in report["violations"])


def test_dynamic_import_with_non_literal_argument_is_flagged(
    project_factory: Callable[..., Path],
) -> None:
    """A computed module name cannot be proven innocent by static analysis."""
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text(
        "import importlib\n"
        "def load(suffix):\n"
        "    return importlib.import_module('example.adapters.' + suffix)\n",
        encoding="utf-8",
    )

    report = analyze(root)

    assert any("opaco" in item for item in report["violations"])


def test_dynamic_import_of_external_module_is_not_flagged(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text(
        "import importlib\ndef load():\n    return importlib.import_module('yaml')\n",
        encoding="utf-8",
    )

    report = analyze(root)

    assert not any("import dinámico" in item for item in report["violations"])


def test_allowlisted_module_may_use_deferred_import(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    package = root / "src/example"
    _make_layers(package)
    (package / "domain/model.py").write_text(
        "import importlib\n"
        "def load():\n"
        "    return importlib.import_module('example.adapters.repo')\n",
        encoding="utf-8",
    )
    _allow_cycle(root, ["example.domain.model"])

    report = analyze(root)

    assert not any("import dinámico" in item for item in report["violations"])
