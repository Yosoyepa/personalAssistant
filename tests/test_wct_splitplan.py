"""Tests for `wct split-plan`: facade partition proposals (TEST-007)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.wct.cli import main
from tools.wct.mutate.engine import function_sites, mutation_sites
from tools.wct.splitplan.engine import plan


def function_source(name: str, sites: int) -> str:
    """A function with exactly `sites` mutation sites (one `if` per site)."""
    return f"def {name}(total):\n" + "    if total:\n        pass\n" * sites


def test_function_sites_attributes_scopes(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "LIMIT = 10\n"
        "def outer(total):\n"
        "    if total:\n"
        "        pass\n"
        "    def inner(total):\n"
        "        if total:\n"
        "            pass\n"
        "class Alpha:\n"
        "    def run(self, total):\n"
        "        if total:\n"
        "            pass\n",
        encoding="utf-8",
    )

    assert function_sites(source) == {
        "<module>": 1,
        "outer": 1,
        "outer.inner": 1,
        "Alpha.run": 1,
    }


@pytest.mark.parametrize(
    "code",
    [
        "VALUE = 2 + 3\n\ndef compute(amount):\n"
        "    if amount > 0 and amount < 9:\n        return amount + 1\n    return 0\n",
        "class Repo:\n    def find(self, key):\n"
        "        if key is None:\n            return None\n        return key\n",
    ],
)
def test_function_sites_sum_equals_mutation_sites(tmp_path: Path, code: str) -> None:
    source = tmp_path / "sample.py"
    source.write_text(code, encoding="utf-8")

    assert sum(function_sites(source).values()) == mutation_sites(source)


def test_plan_groups_functions_within_limit(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    (root / "src/worker.py").write_text(
        function_source("parse", 40)
        + function_source("validate", 35)
        + function_source("emit", 30),
        encoding="utf-8",
    )

    report = plan(root, root / "src/worker.py")

    assert report["ok"] is True
    assert len(report["parts"]) == 2
    assert report["parts"][0]["functions"] == ["parse", "validate"]
    assert report["parts"][0]["sites"] == 75
    assert report["parts"][1]["functions"] == ["emit"]
    assert all(part["sites"] <= report["limit"] for part in report["parts"])
    assert report["facade_imports"] == [
        "from .worker_part1 import parse, validate",
        "from .worker_part2 import emit",
    ]


def test_plan_single_part_when_within_budget(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    (root / "src/tiny.py").write_text(function_source("only", 50), encoding="utf-8")

    report = plan(root, root / "src/tiny.py")

    assert report["ok"] is True
    assert len(report["parts"]) == 1


def test_plan_flags_function_over_limit_alone(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    (root / "src/monster.py").write_text(
        function_source("beast", 140), encoding="utf-8"
    )

    report = plan(root, root / "src/monster.py")

    assert report["ok"] is False
    assert report["oversize_functions"] == [{"function": "beast", "sites": 140}]
    assert "parte la función" in report["message"]


def test_split_plan_cli_json_and_exit_codes(
    project_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = project_factory()
    monkeypatch.setenv("WCT_PROJECT_ROOT", str(root))
    (root / "src/worker.py").write_text(
        function_source("parse", 40)
        + function_source("validate", 35)
        + function_source("emit", 30),
        encoding="utf-8",
    )
    (root / "src/monster.py").write_text(
        function_source("beast", 140), encoding="utf-8"
    )

    assert main(["split-plan", "src/worker.py", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert len(document["parts"]) == 2

    assert main(["split-plan", "src/monster.py"]) == 1
