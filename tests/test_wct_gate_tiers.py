"""Tier composition contracts: the pr tier mirrors what PR CI runs (adapted for pilot)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.wct.cli import parser
from tools.wct.gate.runner import TIERS, gate_coverage_diff
from tools.wct.model import Status


def test_pr_tier_extends_commit_and_adds_pr_ci_gates() -> None:
    extra = set(TIERS["pr"]) - set(TIERS["commit"])

    assert set(TIERS["commit"]).issubset(set(TIERS["pr"]))
    assert extra == {
        "G-HOOKS-WIRED",
        "G-COV-TOTAL",
        "G-COV-DIFF",
        "G-REDTEAM",
    }
    assert len(TIERS["pr"]) == 21


def test_pr_tier_generates_coverage_before_diff() -> None:
    """diff-cover consumes build/coverage/lcov.info: the producer runs first."""
    gates = TIERS["pr"]

    assert gates.index("G-COV-TOTAL") < gates.index("G-COV-DIFF")


def test_cli_accepts_pr_tier() -> None:
    args = parser().parse_args(["gate", "--tier", "pr"])

    assert args.tier == "pr"


def test_missing_diff_cover_is_error_not_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pr tier promises CI parity: a missing tool must block, not skip."""
    monkeypatch.setattr("tools.wct.gate.runner.shutil.which", lambda _: None)

    result = gate_coverage_diff(tmp_path)

    assert result.status is Status.ERROR
    assert "diff-cover" in result.summary


def test_unresolvable_base_is_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/diff-cover"
    )
    monkeypatch.setattr("tools.wct.gate.runner.remote_base", lambda _root: None)

    result = gate_coverage_diff(tmp_path)

    assert result.status is Status.ERROR
    assert "base" in result.summary


def test_command_uses_resolved_base_and_includes_untracked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/diff-cover"
    )
    monkeypatch.setattr(
        "tools.wct.gate.runner.remote_base", lambda _root: "origin/main"
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.wct.gate.runner.subprocess.run", fake_run)

    result = gate_coverage_diff(tmp_path)

    assert result.status is Status.PASS
    command = captured["command"]
    assert "--include-untracked" in command
    assert "--fail-under" in command
    assert "90" in command
    assert "--compare-branch" in command
    assert command[command.index("--compare-branch") + 1] == "origin/main"
