from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools.wct.archmetrics.analyzer import analyze as analyze_architecture
from tools.wct.introvert.analyzer import analyze as analyze_tests
from tools.wct.ratchet.engine import baseline, compare, debt_findings, suppression_count
from tools.wct.util.git import head_sha

MIN_APPROVER = 2
MIN_REASON = 12


def measurements(root: Path) -> dict[str, float]:
    architecture = analyze_architecture(root)
    tests = analyze_tests(root)
    return {
        "suppressions": float(suppression_count(root)),
        "debt-markers": float(len(debt_findings(root))),
        "introverted-tests": float(tests["counts"].get("introverted", 0)),
        "archmetrics-zones": float(
            sum(item["zone"] != "healthy" for item in architecture["metrics"])
        ),
    }


def check(root: Path) -> list[str]:
    failures: list[str] = []
    for name, current in measurements(root).items():
        expected = baseline(root, name)
        if not compare(current, expected):
            failures.append(f"{name}: actual={current:g}, baseline={expected['value']}")
    return failures


def record(root: Path, approved_by: str, reason: str) -> list[Path]:
    if len(approved_by.strip()) < MIN_APPROVER or len(reason.strip()) < MIN_REASON:
        raise ValueError("approved-by y reason >=12 caracteres son obligatorios")
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
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    with (root / "governance/ratchet-log.md").open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n## {datetime.now(UTC).isoformat()}\n\n"
            f"- Approved by: {approved_by}\n- Reason: {reason}\n"
        )
    return written
