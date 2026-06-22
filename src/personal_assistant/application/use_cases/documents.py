"""Document extraction and guarded summarization for small files."""

from __future__ import annotations

from uuid import uuid4

from personal_assistant.application.dto.documents import DocumentInput, DocumentSummary
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.guardrails import scan_prompt
from personal_assistant.domain.common.identity import Principal


class DocumentService:
    """Processes small text-like documents without granting them tool authority."""

    max_bytes = 512_000

    def summarize(self, principal: Principal, document: DocumentInput) -> DocumentSummary:
        if len(document.content) > self.max_bytes:
            raise ValueError("document exceeds small-document limit")

        text = document.content.decode("utf-8", errors="replace")
        warnings: list[str] = []
        scan = scan_prompt(text)
        if scan.blocked:
            warnings.append("document_contains_untrusted_instructions")

        words = text.split()
        summary = " ".join(words[:60])
        if len(words) > 60:
            summary = f"{summary}..."
        if not summary:
            raise AssistantError(ErrorCode.VALIDATION_FAILED, "document has no extractable text")

        return DocumentSummary(
            document_id=f"doc_{uuid4().hex}",
            tenant_id=principal.tenant_id,
            filename=document.filename,
            summary=summary,
            citations=[f"{document.filename}:1"],
            blocked=False,
            warnings=warnings,
        )
