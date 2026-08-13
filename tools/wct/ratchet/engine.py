from __future__ import annotations

import json
import re
import tokenize
from pathlib import Path
from typing import Any

from tools.wct.config import load_config

SUPPRESSION = re.compile(
    r"#\s*(?:noqa|type:\s*ignore|pragma:\s*no cover|nosec|fmt:\s*off)"
    r"|@pytest\.mark\.(?:skip|xfail)",
    re.IGNORECASE,
)
JUSTIFIED = re.compile(r"(?:—|--|because|reason:|justification:).{12,}", re.IGNORECASE)
DEBT = re.compile(r"\b(?:ponytail:|TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)
DEBT_FORMAT = re.compile(
    r"(?:TODO|FIXME|HACK|XXX)\(owner=[^,]+,\s*issue=(?:#\d+|https?://[^)]+)\):|ponytail:\s*owner=[^,]+,\s*issue=(?:#\d+|https?://\S+)",
    re.IGNORECASE,
)


def source_files(root: Path) -> list[Path]:
    _root, policy, _thresholds = load_config(root)
    selected: list[Path] = []
    for key in ("source", "tests"):
        for directory in policy["paths"].get(key, []):
            selected.extend((root / directory).rglob("*.py"))
    return sorted(path for path in selected if path.is_file())


def suppression_findings(root: Path) -> list[str]:
    _root, _policy, thresholds = load_config(root)
    minimum = int(thresholds["suppressions"]["min_justification_chars"])
    findings: list[str] = []
    for path in source_files(root):
        for number, line in _policy_lines(path):
            if SUPPRESSION.search(line) and (
                not JUSTIFIED.search(line) or len(line.split("—")[-1].strip()) < minimum
            ):
                findings.append(
                    f"{path.relative_to(root)}:{number}: supresión sin regla y justificación"
                )
    return findings


def suppression_count(root: Path) -> int:
    """Count every suppression, including justified existing ones."""
    return sum(
        bool(SUPPRESSION.search(line))
        for path in source_files(root)
        for _number, line in _policy_lines(path)
    )


def debt_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in source_files(root):
        for number, line in _policy_lines(path):
            if DEBT.search(line) and not DEBT_FORMAT.search(line):
                findings.append(f"{path.relative_to(root)}:{number}: deuda sin owner + issue")
    return findings


def _policy_lines(path: Path) -> list[tuple[int, str]]:
    """Return comments and pytest decorators without matching string fixtures."""
    selected: list[tuple[int, str]] = []
    with path.open("rb") as stream:
        try:
            selected.extend(
                (token.start[0], token.string)
                for token in tokenize.tokenize(stream.readline)
                if token.type == tokenize.COMMENT
            )
        except (tokenize.TokenError, IndentationError):
            return selected
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("@pytest.mark."):
            selected.append((number, line))
    return selected


def baseline(root: Path, name: str) -> dict[str, Any]:
    value = json.loads((root / "governance/baselines" / f"{name}.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"baseline {name} debe ser un objeto JSON")
    return value


def compare(current: float, base: dict[str, Any]) -> bool:
    direction = base["direction"]
    value = float(base["value"])
    return current <= value if direction == "lower_is_better" else current >= value
