"""Filesystem-backed reply catalog loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personal_assistant.application.services.replies import CatalogValue


def build_reply_catalog(reply_root: Path) -> dict[str, CatalogValue]:
    registry_path = reply_root / "registry.json"
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    replies = raw.get("replies")
    if not isinstance(replies, dict):
        raise ValueError("reply registry must contain a replies object")
    return {str(reply_id): _reply_from_entry(reply_root, str(reply_id), entry) for reply_id, entry in replies.items()}


def _reply_from_entry(root: Path, reply_id: str, entry: Any) -> CatalogValue:
    if not isinstance(entry, dict):
        raise ValueError(f"reply registry entry must be an object: {reply_id}")
    relative_path = str(entry.get("path") or "").strip()
    if not relative_path:
        raise ValueError(f"reply registry entry is missing path: {reply_id}")
    text = (root / relative_path).read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) > 1:
        return lines
    return text.strip()
