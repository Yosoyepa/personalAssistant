from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools.wct.archmetrics.analyzer import analyze as analyze_architecture
from tools.wct.integrity.engine import require_approval_evidence
from tools.wct.introvert.analyzer import analyze as analyze_tests
from tools.wct.ratchet.engine import baseline, compare, debt_findings, suppression_count
from tools.wct.util.git import head_sha

MIN_APPROVER = 3
MIN_REASON = 12


def measurements(root: Path) -> dict[str, object]:
    return {
        "debt-deferred": {"count": len(debt_findings(root))},
        "suppression-count": {"count": suppression_count(root)},
        "test-introversion": analyze_tests(root),
        "architecture-metrics": analyze_architecture(root),
    }


def check(root: Path) -> list[str]:
    failures: list[str] = []
    for name, current in measurements(root).items():
        expected = baseline(root, name)
        if not compare(current, expected):
            failures.append(f"{name}: actual={current}, baseline={expected['value']}")
    return failures


def record(root: Path, approved_by: str, reason: str) -> list[Path]:
    if len(approved_by.strip()) < MIN_APPROVER or len(reason.strip()) < MIN_REASON:
        raise ValueError("approved-by y reason >=12 caracteres son obligatorios")
    require_approval_evidence(reason)
    written: list[Path] = []
    for name, current in measurements(root).items():
        path = root / "governance/baselines" / f"{name}.json"
        document = baseline(root, name)
        document.update(
            {
                "value": current,
                "recorded_at": datetime.now(UTC).isoformat(),
                "recorded_by": approved_by,
                "commit": head_sha(root),
            }
        )
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written.append(path)
    with (root / "governance/ratchet-log.md").open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n## {datetime.now(UTC).isoformat()}\n\n"
            f"- Approved by: {approved_by}\n- Reason: {reason}\n"
        )
    return written
