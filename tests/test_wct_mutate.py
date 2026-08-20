"""Tests for WCT mutation scanning, AST fingerprinting, schema 2, and differential gate."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.wct.gate.runner import gate_mutation_sites
from tools.wct.model import Status
from tools.wct.mutate.engine import (
    function_hashes,
    mutation_sites,
    scan,
    update_manifest,
)


def test_mutation_scan_counts_behavioral_sites(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        """def choose(value):
    if value > 3 and value < 9:
        return value + 1
    return 0
""",
        encoding="utf-8",
    )

    assert mutation_sites(source) >= 6
    assert len(function_hashes(source, tmp_path)) == 1


def test_function_identity_survives_line_shift(tmp_path: Path) -> None:
    """Adding an import above must not invalidate every function below it."""
    source = tmp_path / "sample.py"
    source.write_text("def keep():\n    return 1\n", encoding="utf-8")
    before = function_hashes(source, tmp_path)
    source.write_text(
        "# padding\n# padding\n\ndef keep():\n    return 1\n", encoding="utf-8"
    )

    assert function_hashes(source, tmp_path) == before


def test_function_body_change_invalidates_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def keep():\n    return 1\n", encoding="utf-8")
    before = function_hashes(source, tmp_path)
    source.write_text("def keep():\n    return 2\n", encoding="utf-8")
    after = function_hashes(source, tmp_path)

    assert set(after) == set(before)
    assert after != before


def test_same_method_name_in_different_classes_keeps_distinct_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "class Alpha:\n    def run(self):\n        return 1\n"
        "class Beta:\n    def run(self):\n        return 2\n",
        encoding="utf-8",
    )

    keys = set(function_hashes(source, tmp_path))

    assert keys == {"sample.py::Alpha.run", "sample.py::Beta.run"}


def test_scan_treats_legacy_manifest_as_pending_migration(
    project_factory: Callable[..., Path],
) -> None:
    """A schema-1 manifest (lineno keys) matches nothing.

    Everything counts as changed until `update-manifest` migrates it.
    """
    root = project_factory()
    (root / "src/code.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    manifest = root / "governance/generated/mutation-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "functions": {"src/personal_assistant/code.py::value:1": "dead"},
            }
        ),
        encoding="utf-8",
    )

    report = scan(root)

    assert report["changed_functions"] == 1


def test_update_manifest_without_approval_leaves_lock_alone(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    (root / "src/code.py").write_text("def value():\n    return 1\n", encoding="utf-8")

    path = update_manifest(root)

    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    assert not (root / "governance/integrity.lock").exists()


def test_update_manifest_with_approval_regenerates_lock_and_logs(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    (root / "src/code.py").write_text("def value():\n    return 1\n", encoding="utf-8")

    update_manifest(root, approved_by="mantenedor", reason="aprobado en PR #70")

    assert (root / "governance/integrity.lock").is_file()
    log = (root / "governance/integrity-log.md").read_text(encoding="utf-8")
    assert "PR #70" in log


def test_update_manifest_rejects_partial_approval(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    with pytest.raises(ValueError, match="juntos"):
        update_manifest(root, approved_by="mantenedor")


def test_legacy_file_over_limit_without_changed_functions_does_not_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TEST-007 covers CHANGED files.

    A legacy file over the site budget that the diff did not touch must not
    block the gate.
    """
    report = {
        "files": [
            {
                "file": "src/legacy/http.py",
                "sites": 150,
                "over_limit": True,
                "changed_functions": [],
            }
        ],
        "over_limit": ["src/legacy/http.py"],
    }
    monkeypatch.setattr("tools.wct.gate.runner.scan_mutations", lambda _root: report)

    result = gate_mutation_sites(tmp_path)

    assert result.status is Status.PASS


def test_file_over_limit_with_changed_functions_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = {
        "files": [
            {
                "file": "src/mine/worker.py",
                "sites": 150,
                "over_limit": True,
                "changed_functions": ["src/mine/worker.py::run"],
            }
        ],
        "over_limit": ["src/mine/worker.py"],
    }
    monkeypatch.setattr("tools.wct.gate.runner.scan_mutations", lambda _root: report)

    result = gate_mutation_sites(tmp_path)

    assert result.status is Status.FAIL
    assert "src/mine/worker.py" in result.summary


@pytest.mark.parametrize(
    ("changed", "expected"),
    [([], Status.PASS), (["src/a.py::f"], Status.FAIL)],
)
def test_blocking_depends_on_changed_functions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed: list[str],
    expected: Status,
) -> None:
    monkeypatch.setattr(
        "tools.wct.gate.runner.scan_mutations",
        lambda _root: {
            "files": [
                {
                    "file": "src/a.py",
                    "sites": 101,
                    "over_limit": True,
                    "changed_functions": changed,
                }
            ],
            "over_limit": ["src/a.py"],
        },
    )

    assert gate_mutation_sites(tmp_path).status is expected
