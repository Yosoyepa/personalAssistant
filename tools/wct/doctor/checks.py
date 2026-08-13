from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from tools.wct.config import load_config
from tools.wct.integrity.engine import violations
from tools.wct.rules.engine import drift


def diagnose(root: Path) -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []
    try:
        _root, policy, _thresholds = load_config(root)
        checks.append((True, "governance YAML válido"))
    except Exception as exc:
        return [(False, f"configuración: {exc}")]
    checks.append(
        (sys.version_info >= (3, 11), f"Python {sys.version.split()[0]} (requiere >=3.11)")
    )
    checks.append((shutil.which("git") is not None, "git disponible"))
    checks.append((shutil.which("uv") is not None, "uv disponible"))
    checks.append((not drift(root), "reglas por proveedor sincronizadas"))
    settings = root / ".claude/settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            hook_map = data.get("hooks", {})
            events = set(hook_map)
            required = {
                "PreToolUse",
                "PostToolUse",
                "Stop",
                "SubagentStart",
                "SubagentStop",
                "ConfigChange",
            }
            checks.append((required <= events, f"hooks cableados ({len(events)} eventos)"))
            expected_commands = {
                "PreToolUse": "wct hook pre-tool-use",
                "PostToolUse": "wct hook post-tool-use",
                "Stop": "wct hook stop",
                "SubagentStart": "wct hook subagent-start",
                "SubagentStop": "wct hook subagent-stop",
                "ConfigChange": "wct hook config-change",
            }
            wired = all(
                expected in json.dumps(hook_map.get(event, []))
                for event, expected in expected_commands.items()
            )
            checks.append((wired, "cada evento requerido invoca su guard WCT exacto"))
        except (OSError, json.JSONDecodeError) as exc:
            checks.append((False, f".claude/settings.json inválido: {exc}"))
    else:
        checks.append((False, "falta .claude/settings.json"))
    checks.append((not violations(root), "integrity.lock coincide"))
    for tool in ("ruff", "mypy", "pytest"):
        checks.append((shutil.which(tool) is not None, f"{tool} disponible"))
    if policy.get("minimalism_mode") == "ultra":
        checks.append((False, "minimalism_mode ultra está prohibido por ADR-001"))
    return checks
