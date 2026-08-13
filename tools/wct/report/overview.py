from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.wct.config import load_config
from tools.wct.gate.runner import REGISTRY, TIERS
from tools.wct.rules.engine import rule_documents


def overview(root: Path) -> dict[str, Any]:
    _root, policy, _thresholds = load_config(root)
    automated: list[str] = []
    human: list[str] = []
    unknown: list[str] = []
    for document in rule_documents(root):
        for rule in document.get("rules", []):
            checks = set(rule.get("verified_by", []))
            if checks == {"human"}:
                human.append(rule["id"])
            elif checks - ({"human"} | set(REGISTRY)):
                unknown.append(rule["id"])
            else:
                automated.append(rule["id"])
    return {
        "profile": policy["profile"],
        "mode": policy["mode"],
        "rules": {"automated": automated, "human_only": human, "unknown": unknown},
        "tiers": TIERS,
        "disabled_gates": policy.get("gates", {}).get("disabled", []),
        "optional_tools": policy.get("gates", {}).get("optional_tools", []),
    }
