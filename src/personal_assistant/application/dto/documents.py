"""Document use-case DTOs."""

from __future__ import annotations

from pydantic import Field

from personal_assistant.application.dto.base import ApplicationDTO


class DocumentInput(ApplicationDTO):
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    content_type: str = "text/plain"


class DocumentSummary(ApplicationDTO):
    document_id: str
    tenant_id: str
    filename: str
    summary: str
    citations: list[str]
    blocked: bool = False
    warnings: list[str] = Field(default_factory=list)
