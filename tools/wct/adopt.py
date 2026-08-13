from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def inspect_repository(target: Path) -> dict[str, Any]:
    ignored = {".git", ".venv", "node_modules", "vendor", "build", "dist"}

    def count(*suffixes: str) -> int:
        return sum(
            path.suffix in suffixes and not (set(path.relative_to(target).parts) & ignored)
            for path in target.rglob("*")
            if path.is_file()
        )

    languages = {
        "python": count(".py"),
        "typescript": count(".ts", ".tsx"),
        "go": count(".go"),
        "java": count(".java"),
        "clojure": count(".clj"),
    }
    source_candidates = [name for name in ("src", "app", "lib") if (target / name).is_dir()]
    test_candidates = [name for name in ("tests", "test", "spec") if (target / name).is_dir()]
    agents = [
        name
        for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md")
        if (target / name).exists()
    ]
    governed = (target / "governance/policy.yaml").is_file()
    return {
        "target": str(target.resolve()),
        "languages": {name: count for name, count in languages.items() if count},
        "source_candidates": source_candidates,
        "test_candidates": test_candidates,
        "existing_agent_rules": agents,
        "has_ci": (target / ".github/workflows").is_dir(),
        "has_governance": governed,
        "recommendation": "configure-and-measure" if governed else "copy-template-then-configure",
    }


def render_inventory(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)
