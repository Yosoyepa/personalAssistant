"""Probe one inactive protocol tool against the real ToolCall schema."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from personal_assistant.application.dto.runtime import ToolCall
from personal_assistant.evals.schema import StrictModel


class InputModel(StrictModel):
    toolName: Literal["mcp.search", "a2a.delegate"]


class ExpectedModel(StrictModel):
    rejected: bool


def execute(value: InputModel) -> dict[str, object]:
    try:
        ToolCall(name=value.toolName)
    except ValidationError:
        return {"rejected": True}
    return {"rejected": False}
