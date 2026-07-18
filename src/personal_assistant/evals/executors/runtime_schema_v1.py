"""Probe whether unstructured output can satisfy the runtime contract."""

from __future__ import annotations

from pydantic import ValidationError

from personal_assistant.application.dto.runtime import PersonalAssistantRunResult
from personal_assistant.evals.schema import StrictModel


class InputModel(StrictModel):
    candidate: object


class ExpectedModel(StrictModel):
    rejected: bool


def execute(value: InputModel) -> dict[str, object]:
    try:
        PersonalAssistantRunResult.model_validate(value.candidate)
    except ValidationError:
        return {"rejected": True}
    return {"rejected": False}
