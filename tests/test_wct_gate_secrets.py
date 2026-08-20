"""Tests for G-SECRET parsing of detect-secrets --slim and read-only baseline handling."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.wct.gate.runner import gate_secrets
from tools.wct.model import Status


def test_secrets_finding_without_line_number_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--slim omits line_number; the gate must not crash on it."""
    document = json.dumps(
        {"results": {"src/config.py": [{"type": "Keyword", "hashed_secret": "x"}]}}
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=document, stderr="")

    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/detect-secrets"
    )
    monkeypatch.setattr("tools.wct.gate.runner.subprocess.run", fake_run)

    result = gate_secrets(tmp_path)

    assert result.status is Status.FAIL
    assert "src/config.py:?: posible Keyword" in result.details


def test_secrets_finding_with_line_number_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = json.dumps(
        {"results": {"src/config.py": [{"type": "Keyword", "line_number": 12}]}}
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=document, stderr="")

    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/detect-secrets"
    )
    monkeypatch.setattr("tools.wct.gate.runner.subprocess.run", fake_run)

    result = gate_secrets(tmp_path)

    assert result.status is Status.FAIL
    assert "src/config.py:12: posible Keyword" in result.details


def test_secrets_empty_stdout_means_no_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With every finding audited in the baseline, detect-secrets prints nothing."""

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/detect-secrets"
    )
    monkeypatch.setattr("tools.wct.gate.runner.subprocess.run", fake_run)

    result = gate_secrets(tmp_path)

    assert result.status is Status.PASS


def test_secrets_excludes_findings_already_audited_in_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Triaged findings in .secrets.baseline do not block the gate.

    The baseline is read WITHOUT rewriting it (G-META-1 protects it).
    """
    document = json.dumps(
        {"results": {"src/config.py": [{"type": "Keyword", "hashed_secret": "dead"}]}}
    )
    (tmp_path / ".secrets.baseline").write_text(
        json.dumps(
            {
                "results": {
                    "src/config.py": [{"type": "Keyword", "hashed_secret": "dead"}]
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert "--baseline" not in command
        return subprocess.CompletedProcess(command, 0, stdout=document, stderr="")

    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/detect-secrets"
    )
    monkeypatch.setattr("tools.wct.gate.runner.subprocess.run", fake_run)

    result = gate_secrets(tmp_path)

    assert result.status is Status.PASS


def test_secrets_reports_findings_missing_from_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = json.dumps(
        {"results": {"src/config.py": [{"type": "Keyword", "hashed_secret": "live"}]}}
    )
    (tmp_path / ".secrets.baseline").write_text(
        json.dumps(
            {
                "results": {
                    "src/config.py": [{"type": "Keyword", "hashed_secret": "dead"}]
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=document, stderr="")

    monkeypatch.setattr(
        "tools.wct.gate.runner.shutil.which", lambda _: "/usr/bin/detect-secrets"
    )
    monkeypatch.setattr("tools.wct.gate.runner.subprocess.run", fake_run)

    result = gate_secrets(tmp_path)

    assert result.status is Status.FAIL
    assert any("config.py" in line for line in result.details)
