"""Integration tests for `wct fmt` changeset restriction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools.wct.fmt.engine import run as fmt_run


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _fake_ruff(monkeypatch: pytest.MonkeyPatch, commands: list[list[str]]) -> None:
    real_run = subprocess.run

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] != "ruff":
            return real_run(command, **kwargs)
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.wct.fmt.engine.shutil.which", lambda _: "/usr/bin/ruff")
    monkeypatch.setattr("tools.wct.fmt.engine.subprocess.run", fake_run)


def test_fmt_staged_mode_formats_only_staged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    (root / "b.py").write_text("y=2\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "add", "a.py")
    commands: list[list[str]] = []
    _fake_ruff(monkeypatch, commands)

    assert fmt_run(root, staged_only=True) == 0

    target = commands[0]
    assert "a.py" in target
    assert "b.py" not in target


def test_fmt_default_mode_covers_working_changeset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    (root / "b.py").write_text("y=2\n", encoding="utf-8")
    (root / "c.txt").write_text("z\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "add", "a.py")
    commands: list[list[str]] = []
    _fake_ruff(monkeypatch, commands)

    assert fmt_run(root) == 0

    target = commands[0]
    assert "a.py" in target
    assert "b.py" in target
    assert not any(name.endswith(".txt") for name in target)


def test_fmt_without_python_changes_does_not_spawn_ruff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init")
    commands: list[list[str]] = []
    _fake_ruff(monkeypatch, commands)

    assert fmt_run(root) == 0
    assert commands == []
    assert "sin cambios" in capsys.readouterr().out


def test_fmt_missing_ruff_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "add", "a.py")
    monkeypatch.setattr("tools.wct.fmt.engine.shutil.which", lambda _: None)

    assert fmt_run(root) == 2
