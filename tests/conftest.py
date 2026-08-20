"""Pytest fixtures for project testing."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def project_factory(tmp_path: Path) -> object:
    repo_root = Path(__file__).parents[1]

    def create(*, package: str = "example") -> Path:
        root = tmp_path / package
        root.mkdir()
        governance = root / "governance"
        governance.mkdir()
        shutil.copy(repo_root / "governance/policy.yaml", governance / "policy.yaml")
        shutil.copy(
            repo_root / "governance/thresholds.yaml", governance / "thresholds.yaml"
        )
        shutil.copytree(repo_root / "governance/baselines", governance / "baselines")
        shutil.copytree(repo_root / "governance/rules", governance / "rules")
        policy = yaml.safe_load(
            (governance / "policy.yaml").read_text(encoding="utf-8")
        )
        policy["architecture"]["root_package"] = package
        policy["providers"] = []
        (governance / "policy.yaml").write_text(
            yaml.safe_dump(policy, sort_keys=False), encoding="utf-8"
        )
        for directory in ("src", "tests", "features"):
            (root / directory).mkdir()
        return root

    return create
