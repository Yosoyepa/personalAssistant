"""Filesystem-backed prompt catalog loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personal_assistant.application.services.prompts import PromptTemplate, StaticPromptCatalog


DEFAULT_PROMPT_ROOT = Path(__file__).resolve().parents[3] / "prompts"


def build_prompt_catalog(prompt_root: Path | None = None) -> StaticPromptCatalog:
    root = prompt_root or DEFAULT_PROMPT_ROOT
    registry_path = root / "registry.json"
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    prompts = raw.get("prompts")
    if not isinstance(prompts, dict):
        raise ValueError("prompt registry must contain a prompts object")

    templates: dict[str, PromptTemplate] = {}
    for prompt_id, entry in prompts.items():
        if not isinstance(entry, dict):
            raise ValueError(f"prompt registry entry must be an object: {prompt_id}")
        templates[prompt_id] = _template_from_entry(root, str(prompt_id), entry)
    return StaticPromptCatalog(templates)


def _template_from_entry(root: Path, prompt_id: str, entry: dict[str, Any]) -> PromptTemplate:
    version = str(entry.get("version") or "").strip()
    relative_path = str(entry.get("path") or "").strip()
    raw_variables = entry.get("required_variables", [])
    if not version or not relative_path:
        raise ValueError(f"prompt registry entry is missing version/path: {prompt_id}")
    if not isinstance(raw_variables, list) or not all(isinstance(item, str) for item in raw_variables):
        raise ValueError(f"prompt registry variables must be a string list: {prompt_id}")
    template_path = root / relative_path
    return PromptTemplate(
        prompt_id=prompt_id,
        version=version,
        template=template_path.read_text(encoding="utf-8"),
        required_variables=tuple(raw_variables),
    )
