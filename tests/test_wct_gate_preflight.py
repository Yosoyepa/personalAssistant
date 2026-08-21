"""Preflight: a tier with declared env prerequisites fails fast and clear."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tools.wct.gate.runner import TIERS, run_tier
from tools.wct.model import GateResult, Status


def _factory_with_requirement(
    project_factory: Callable[..., Path], variable: str
) -> Path:
    root = project_factory()
    policy_path = root / "governance/policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["environment_required"] = {"fast": [variable]}
    policy.setdefault("gates", {})["disabled"] = []
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return root


@pytest.fixture
def _green_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_gate(gate_id: str) -> Callable[[Path], GateResult]:
        return lambda _root: GateResult(gate_id, Status.PASS, "ok")

    monkeypatch.setattr(
        "tools.wct.gate.runner.REGISTRY",
        {gate_id: fake_gate(gate_id) for gate_id in TIERS["fast"]},
    )


@pytest.mark.usefixtures("_green_gates")
def test_missing_required_variable_fails_tier_before_running_gates(
    project_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _factory_with_requirement(project_factory, "WCT_TEST_DSN")
    monkeypatch.delenv("WCT_TEST_DSN", raising=False)

    results = run_tier(root, "fast")

    assert len(results) == 1
    assert results[0].gate_id == "G-ENV"
    assert results[0].status is Status.ERROR
    assert "WCT_TEST_DSN" in results[0].summary


@pytest.mark.usefixtures("_green_gates")
def test_satisfied_requirements_run_gates_normally(
    project_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _factory_with_requirement(project_factory, "WCT_TEST_DSN")
    monkeypatch.setenv("WCT_TEST_DSN", "postgres://test")

    results = run_tier(root, "fast")

    assert [result.gate_id for result in results] == TIERS["fast"]
    assert all(result.status is Status.PASS for result in results)
