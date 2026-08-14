from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.wct.config import find_root, load_config

PROHIBITED_BASH = [
    (re.compile(r"(?:^|\s)git\s+(?:commit|push)\b[^\n]*--no-verify\b"), "bypass --no-verify"),
    (re.compile(r"(?:^|\s)git\s+reset\s+--hard\b"), "git reset --hard"),
    (
        re.compile(r"(?:^|\s)(?:rm|find)\b[^\n]*(?:governance|\.git|integrity\.lock)"),
        "borrado de control plane",
    ),
    (
        re.compile(
            r"(?:sed\s+-i|perl\s+-pi|python[^\n]*-c)[^\n]*(?:governance|integrity\.lock|thresholds\.yaml)"
        ),
        "edición indirecta de control plane",
    ),
    (
        re.compile(r"(?:^|\s)(?:uv\s+run\s+)?wct\s+integrity\s+(?:lock|bless)\b"),
        "auto-aprobación de integridad; ejecútala un humano fuera del agente",
    ),
    (
        re.compile(r"(?:^|\s)(?:uv\s+run\s+)?wct\s+ratchet\s+record\b"),
        "auto-aprobación de baseline; ejecútala un humano fuera del agente",
    ),
]


def _read() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("hook input debe ser un objeto JSON")
    return value


def _block(reason: str) -> int:
    print(f"WCT BLOCK: {reason}", file=sys.stderr)
    return 2


def _path_is_protected(root: Path, raw: str) -> bool:
    _root, policy, _thresholds = load_config(root)
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return False
    return any(fnmatch.fnmatch(relative, pattern) for pattern in policy["paths"]["protected"])


def pre_tool_use(root: Path, payload: dict[str, Any]) -> int:
    name = str(payload.get("tool_name", ""))
    data = payload.get("tool_input") or {}
    if name in {"Edit", "Write", "NotebookEdit"}:
        target = str(data.get("file_path") or data.get("path") or "")
        if target and _path_is_protected(root, target):
            return _block(
                f"{target} está protegido; usa `wct integrity bless` con aprobación humana"
            )
    if name in {"Bash", "exec_command", "shell"}:
        command = str(data.get("command") or data.get("cmd") or "")
        for pattern, label in PROHIBITED_BASH:
            if pattern.search(command):
                return _block(f"comando prohibido: {label}")
    return 0


def _run_gate(root: Path, tier: str) -> int:
    command = [sys.executable, "-m", "tools.wct", "gate", "--tier", tier, "--quiet"]
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    if completed.returncode:
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return _block(output[-8000:] or f"tier {tier} falló")
    return 0


def _dispatch_unsafe(event: str) -> int:
    root = find_root()
    payload = _read()
    normalized = event.lower().replace("_", "-")
    if normalized == "pre-tool-use":
        return pre_tool_use(root, payload)
    if normalized in {"post-tool-use", "post-tool-batch"}:
        return _run_gate(root, "fast")
    if normalized in {"stop", "subagent-stop"}:
        return _run_gate(root, "commit")
    if normalized == "config-change":
        return _run_gate(root, "fast")
    if normalized in {"session-start", "subagent-start", "post-compact"}:
        rules_path = next(
            (
                root / name
                for name in ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
                if (root / name).is_file()
            ),
            None,
        )
        rules = rules_path.read_text(encoding="utf-8") if rules_path else "Run `wct rules build`."
        print(json.dumps({"hookSpecificOutput": {"additionalContext": rules[:12000]}}))
    return 0


def dispatch(event: str) -> int:
    try:
        result = _dispatch_unsafe(event)
    except Exception as exc:  # deliberate fail-closed behavior
        return _block(f"guard crash ({event}): {type(exc).__name__}: {exc}")
    return result
