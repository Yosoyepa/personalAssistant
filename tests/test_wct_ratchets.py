"""Tests for WCT ratchets, suppressions, debt markers, and baseline recording."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tools.wct.ratchet.engine import (
    debt_findings,
    suppression_count,
    suppression_findings,
)
from tools.wct.ratchet.measure import record


def test_suppression_requires_justification_and_counts_it(
    project_factory: Callable[..., Path],
) -> None:
    root = project_factory()
    source = root / "src/bad.py"
    source.write_text("value = call()  # noqa\n", encoding="utf-8")

    assert suppression_count(root) == 1
    assert len(suppression_findings(root)) == 1


def test_debt_requires_owner_and_issue(project_factory: Callable[..., Path]) -> None:
    root = project_factory()
    source = root / "src/bad.py"
    source.write_text("# TODO: later\n", encoding="utf-8")

    assert len(debt_findings(root)) == 1


def test_ratchet_record_requires_approval_evidence(
    project_factory: Callable[..., Path],
) -> None:
    """Recording a baseline is an approval act: cite where it was approved."""
    root = project_factory()
    with pytest.raises(ValueError, match="evidencia"):
        record(root, "mantenedor", "endurezco el umbral con este texto largo")
