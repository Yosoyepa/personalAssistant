"""Probe the shipped tool surface for forbidden capabilities."""

from __future__ import annotations

from pydantic import Field

from personal_assistant.contracts.tools import (
    CALENDAR_CREATE_CONTRACT,
    NOTIFICATION_SEND_CONTRACT,
)
from personal_assistant.evals.schema import StrictModel


class InputModel(StrictModel):
    forbiddenTools: list[str] = Field(min_length=1)


class ExpectedModel(StrictModel):
    absent: bool


def execute(value: InputModel) -> dict[str, object]:
    shipped = {CALENDAR_CREATE_CONTRACT.name, NOTIFICATION_SEND_CONTRACT.name}
    return {"absent": shipped.isdisjoint(value.forbiddenTools)}
