"""Document extraction and guarded summarization for small files."""

from __future__ import annotations

from uuid import uuid4

from personal_assistant.application.dto.documents import DocumentInput, DocumentSummary
from personal_assistant.domain.common.citations import (
    Citation,
    parse_citation,
    verify_grounding,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.guardrails import scan_prompt
from personal_assistant.domain.common.identity import Principal

SUMMARY_WORD_LIMIT = 60


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
        summary = " ".join(words[:SUMMARY_WORD_LIMIT])
        if len(words) > SUMMARY_WORD_LIMIT:
            summary = f"{summary}..."
        if not summary:
            raise AssistantError(ErrorCode.VALIDATION_FAILED, "document has no extractable text")

        citations = self._grounded_citations(document.filename, text)

        return DocumentSummary(
            document_id=f"doc_{uuid4().hex}",
            tenant_id=principal.tenant_id,
            filename=document.filename,
            summary=summary,
            citations=citations,
            blocked=False,
            warnings=warnings,
        )

    def _grounded_citations(self, filename: str, text: str) -> list[str]:
        """Build citations for the lines that actually feed the summary.

        Every citation is validated (strict parse of the canonical form plus
        grounding verification against the source text) before it is emitted;
        any invalid citation raises instead of producing partial output.
        """
        citations: list[str] = []
        remaining_words = SUMMARY_WORD_LIMIT
        for line_number, line_text in enumerate(text.splitlines(), start=1):
            if remaining_words <= 0:
                break
            line_words = line_text.split()
            if not line_words:
                continue
            excerpt = line_text.strip()[:160]
            citation = Citation(filename=filename, line=line_number, excerpt=excerpt)
            verify_grounding(text, citation)
            validated = parse_citation(citation.canonical())
            verify_grounding(text, validated)
            citations.append(validated.canonical())
            remaining_words -= len(line_words)
        if not citations:
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "document summary has no groundable source lines",
            )
        return citations
