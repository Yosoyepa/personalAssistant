"""Tests for citation parsing, grounding verification, and summary wiring."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from personal_assistant.application.dto.documents import DocumentInput
from personal_assistant.application.use_cases.documents import DocumentService
from personal_assistant.domain.common.citations import (
    Citation,
    parse_citation,
    verify_grounding,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.guardrails import GuardrailViolation
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier


class ParseCitationTests(unittest.TestCase):
    def test_parse_valid_citation(self) -> None:
        citation = parse_citation("notes.md:3")

        self.assertEqual(citation.filename, "notes.md")
        self.assertEqual(citation.line, 3)
        self.assertIsNone(citation.excerpt)
        self.assertEqual(citation.canonical(), "notes.md:3")

    def test_parse_allows_colons_inside_filename(self) -> None:
        citation = parse_citation("dir/report:v2.md:12")

        self.assertEqual(citation.filename, "dir/report:v2.md")
        self.assertEqual(citation.line, 12)

    def test_parse_rejects_malformed_formats(self) -> None:
        malformed = [
            "",
            "notes.md",
            "notes.md:",
            ":3",
            " :3",
            "notes.md:0",
            "notes.md:-1",
            "notes.md:abc",
            "notes.md:3x",
            "notes.md: 3",
        ]
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(AssistantError) as ctx:
                parse_citation(raw)
            self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_FAILED)

    def test_citation_model_rejects_invalid_fields(self) -> None:
        with self.assertRaises(ValidationError):
            Citation(filename="", line=1)
        with self.assertRaises(ValidationError):
            Citation(filename="notes.md", line=0)
        with self.assertRaises(ValidationError):
            Citation(filename="notes.md", line=1, excerpt="x" * 161)


class GroundingTests(unittest.TestCase):
    SOURCE = "alpha one\nbeta two\ngamma three"

    def test_grounding_ok_without_excerpt(self) -> None:
        citation = Citation(filename="doc.txt", line=2)

        verified = verify_grounding(self.SOURCE, citation)

        self.assertEqual(verified, citation)

    def test_grounding_ok_with_excerpt_in_line(self) -> None:
        citation = Citation(filename="doc.txt", line=2, excerpt="beta")

        verified = verify_grounding(self.SOURCE, citation)

        self.assertEqual(verified, citation)

    def test_grounding_rejects_line_out_of_range(self) -> None:
        citation = Citation(filename="doc.txt", line=4)

        with self.assertRaises(GuardrailViolation) as ctx:
            verify_grounding(self.SOURCE, citation)
        self.assertEqual(ctx.exception.code, ErrorCode.GUARDRAIL_BLOCKED)

    def test_grounding_rejects_excerpt_mismatch(self) -> None:
        citation = Citation(filename="doc.txt", line=1, excerpt="gamma")

        with self.assertRaises(GuardrailViolation) as ctx:
            verify_grounding(self.SOURCE, citation)
        self.assertEqual(ctx.exception.code, ErrorCode.GUARDRAIL_BLOCKED)

    def test_grounding_rejects_excerpt_from_another_line(self) -> None:
        citation = Citation(filename="doc.txt", line=3, excerpt="alpha one beta")

        with self.assertRaises(GuardrailViolation):
            verify_grounding(self.SOURCE, citation)

    def test_fail_closed_no_partial_emission(self) -> None:
        """One ungrounded citation invalidates the whole batch."""
        citations = [
            Citation(filename="doc.txt", line=1),
            Citation(filename="doc.txt", line=2),
            Citation(filename="doc.txt", line=99),
        ]
        emitted: list[str] = []

        with self.assertRaises(GuardrailViolation):
            for citation in citations:
                emitted.append(verify_grounding(self.SOURCE, citation).canonical())

        self.assertEqual(emitted, ["doc.txt:1", "doc.txt:2"])
        self.assertNotIn("doc.txt:99", emitted)


class SummaryCitationWiringTests(unittest.TestCase):
    def principal(self, tenant_id: str = "tenant-a") -> Principal:
        return Principal.for_test(
            principal_id="user-1",
            tenant_id=tenant_id,
            permission_tier=PermissionTier.P2,
        )

    def test_single_line_document_keeps_backward_compatible_citation(self) -> None:
        service = DocumentService()

        summary = service.summarize(
            self.principal(),
            DocumentInput(filename="note.txt", content=b"hello world"),
        )

        self.assertEqual(summary.citations, ["note.txt:1"])
        self.assertEqual(summary.tenant_id, "tenant-a")

    def test_summary_citations_point_at_lines_actually_summarized(self) -> None:
        line1 = " ".join(f"w{i}" for i in range(40))
        line2 = " ".join(f"x{i}" for i in range(40))
        line3 = " ".join(f"y{i}" for i in range(40))
        content = f"{line1}\n{line2}\n{line3}".encode()
        service = DocumentService()

        summary = service.summarize(
            self.principal(),
            DocumentInput(filename="doc.txt", content=content),
        )

        # The 60-word summary draws on lines 1 and 2 only; line 3 is not cited.
        self.assertEqual(summary.citations, ["doc.txt:1", "doc.txt:2"])
        self.assertTrue(summary.summary.endswith("..."))

    def test_short_multiline_document_cites_all_content_lines(self) -> None:
        content = b"first line\n\nsecond line\nthird line"
        service = DocumentService()

        summary = service.summarize(
            self.principal(),
            DocumentInput(filename="multi.txt", content=content),
        )

        self.assertEqual(
            summary.citations,
            ["multi.txt:1", "multi.txt:3", "multi.txt:4"],
        )

    def test_every_emitted_citation_parses_and_grounds_against_source(self) -> None:
        content = b"alpha beta gamma\ndelta epsilon\nzeta eta theta iota"
        text = content.decode("utf-8")
        service = DocumentService()

        summary = service.summarize(
            self.principal(),
            DocumentInput(filename="verify.txt", content=content),
        )

        self.assertTrue(summary.citations)
        for raw in summary.citations:
            citation = parse_citation(raw)
            self.assertEqual(citation.filename, "verify.txt")
            verify_grounding(text, citation)

    def test_tenant_id_comes_from_principal(self) -> None:
        service = DocumentService()

        summary = service.summarize(
            self.principal(tenant_id="tenant-z"),
            DocumentInput(filename="note.txt", content=b"tenant_id=evil some text"),
        )

        self.assertEqual(summary.tenant_id, "tenant-z")

    def test_whitespace_only_document_fails_validation(self) -> None:
        service = DocumentService()

        with self.assertRaises(AssistantError) as ctx:
            service.summarize(
                self.principal(),
                DocumentInput(filename="empty.txt", content=b"   \n\t  "),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_FAILED)


if __name__ == "__main__":
    unittest.main()
