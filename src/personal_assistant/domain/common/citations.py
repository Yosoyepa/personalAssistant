"""Formal citation model with strict parsing and grounding verification.

Citations use the canonical ``filename:line`` string format (1-based line
numbers). Grounding verification is fail-closed: a citation that references a
nonexistent line, or whose excerpt does not appear in the referenced line,
raises a GuardrailViolation instead of being silently emitted or dropped.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.guardrails import GuardrailViolation

_CITATION_PATTERN = re.compile(r"^(?P<filename>.+):(?P<line>\d+)$")

CITATION_GUARDRAIL = "citation_grounding"


class Citation(BaseModel):
    """A grounded reference to one line of a source document."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    filename: str = Field(min_length=1, max_length=260)
    line: int = Field(ge=1)
    excerpt: str | None = Field(default=None, max_length=160)

    def canonical(self) -> str:
        """Return the canonical ``filename:line`` string form."""
        return f"{self.filename}:{self.line}"


def parse_citation(raw: str) -> Citation:
    """Parse the canonical ``filename:line`` format strictly.

    Raises AssistantError(VALIDATION_FAILED) on any malformed input.
    """
    match = _CITATION_PATTERN.match(raw.strip())
    if match is None:
        raise AssistantError(
            ErrorCode.VALIDATION_FAILED,
            "invalid citation format; expected 'filename:line' with a positive integer line",
        )
    try:
        return Citation(filename=match.group("filename"), line=int(match.group("line")))
    except ValidationError as exc:
        raise AssistantError(
            ErrorCode.VALIDATION_FAILED,
            "invalid citation; filename must be non-empty and line must be a positive integer",
        ) from exc


def verify_grounding(source_text: str, citation: Citation) -> Citation:
    """Verify a citation against its source document, fail-closed.

    The referenced line must exist (1-based) and, when an excerpt is given,
    the excerpt must appear verbatim in the referenced line. Any violation
    raises GuardrailViolation; nothing is silently emitted or dropped.
    """
    lines = source_text.splitlines()
    if citation.line > len(lines):
        raise GuardrailViolation(
            "citation references a line that does not exist in the source document",
            context={
                "guardrail": CITATION_GUARDRAIL,
                "filename": citation.filename,
                "line": citation.line,
                "line_count": len(lines),
            },
        )
    referenced_line = lines[citation.line - 1]
    if citation.excerpt is not None and citation.excerpt not in referenced_line:
        raise GuardrailViolation(
            "citation excerpt does not appear in the referenced source line",
            context={
                "guardrail": CITATION_GUARDRAIL,
                "filename": citation.filename,
                "line": citation.line,
            },
        )
    return citation
