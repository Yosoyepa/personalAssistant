"""Versioned prompt catalog used by LLM-backed use cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Mapping

from personal_assistant.application.ports.prompts import RenderedPrompt


CONVERSATION_INTENT_PROMPT_ID = "conversation_intent"
REMINDER_EXTRACTION_PROMPT_ID = "reminder_extraction"
DEFAULT_PROMPT_ROOT = Path(__file__).resolve().parents[4] / "prompts"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    prompt_id: str
    version: str
    template: str
    required_variables: tuple[str, ...] = ()


class StaticPromptCatalog:
    """Render versioned prompt templates supplied by the composition root."""

    def __init__(self, templates: Mapping[str, PromptTemplate]) -> None:
        if not templates:
            raise ValueError("prompt catalog requires at least one template")
        self._templates = dict(templates)

    def render(self, prompt_id: str, variables: Mapping[str, object]) -> RenderedPrompt:
        template = self._templates.get(prompt_id)
        if template is None:
            raise KeyError(f"unknown prompt: {prompt_id}")
        missing = [name for name in template.required_variables if name not in variables]
        if missing:
            raise KeyError(f"missing prompt variables for {prompt_id}: {', '.join(missing)}")
        rendered_variables = {key: _format_prompt_value(value) for key, value in variables.items()}
        text = Template(template.template).substitute(rendered_variables).strip()
        return RenderedPrompt(prompt_id=template.prompt_id, version=template.version, text=text)


class DefaultPromptCatalog(StaticPromptCatalog):
    """Prompt catalog loaded from the repository prompt registry."""

    def __init__(self, prompt_root: Path | None = None) -> None:
        super().__init__(_load_prompt_templates(prompt_root or DEFAULT_PROMPT_ROOT))


def _format_prompt_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _load_prompt_templates(prompt_root: Path) -> dict[str, PromptTemplate]:
    registry_path = prompt_root / "registry.json"
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    prompts = raw.get("prompts")
    if not isinstance(prompts, dict):
        raise ValueError("prompt registry must contain a prompts object")
    templates: dict[str, PromptTemplate] = {}
    for prompt_id, entry in prompts.items():
        template = _load_template_entry(prompt_root, str(prompt_id), entry)
        templates[prompt_id] = template
    return templates


def _load_template_entry(prompt_root: Path, prompt_id: str, entry: object) -> PromptTemplate:
    if not isinstance(entry, dict):
        raise ValueError(f"prompt registry entry must be an object: {prompt_id}")
    version = str(entry.get("version") or "").strip()
    relative_path = str(entry.get("path") or "").strip()
    raw_variables = entry.get("required_variables", [])
    if not version or not relative_path:
        raise ValueError(f"prompt registry entry is missing version/path: {prompt_id}")
    if not isinstance(raw_variables, list) or not all(isinstance(item, str) for item in raw_variables):
        raise ValueError(f"prompt registry variables must be a string list: {prompt_id}")
    template = (prompt_root / relative_path).read_text(encoding="utf-8").strip()
    if not template:
        raise ValueError(f"prompt template is empty: {prompt_id}")
    return PromptTemplate(
        prompt_id=prompt_id,
        version=version,
        template=template,
        required_variables=tuple(raw_variables),
    )
