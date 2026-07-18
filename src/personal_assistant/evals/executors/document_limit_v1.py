"""Probe the real document byte-limit boundary."""

from __future__ import annotations

from pydantic import Field

from personal_assistant.application.dto.documents import DocumentInput
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.evals.schema import StrictModel


class InputModel(StrictModel):
    filename: str = Field(min_length=1)
    sizeBytes: int = Field(ge=0, le=1_000_000)


class ExpectedModel(StrictModel):
    rejected: bool


def execute(value: InputModel) -> dict[str, object]:
    service = DocumentService()
    principal = Principal.for_test(
        principal_id="eval-user",
        tenant_id="eval-tenant",
        permission_tier=PermissionTier.P2,
    )
    try:
        service.summarize(
            principal,
            DocumentInput(filename=value.filename, content=b"x" * value.sizeBytes),
        )
    except ValueError:
        return {"rejected": True}
    return {"rejected": False}
