"""Small text normalization helpers shared by admin data and rendering."""

from __future__ import annotations

from typing import Any


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _lower_text(value: Any) -> str:
    return _string_value(value).lower()


def _preview(text: str, *, length: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= length:
        return normalized
    return f"{normalized[: length - 3]}..."


def _error_category_label(category: str) -> str:
    if category == "all":
        return "All"
    if category == "llm":
        return "LLM"
    return category.replace("_", " ").title()
