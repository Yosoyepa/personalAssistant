"""Simple local guardrails for prompt injection and PII detection."""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from personal_assistant.shared.errors import AssistantError, ErrorCode


class GuardrailCategory(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    PII = "pii"


class GuardrailSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GuardrailViolation(AssistantError):
    """Raised when guardrails block unsafe input."""

    def __init__(self, message: str, *, context: dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.GUARDRAIL_BLOCKED, message, context=context)


class GuardrailFinding(BaseModel):
    """One guardrail finding."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    category: GuardrailCategory
    severity: GuardrailSeverity
    label: str = Field(min_length=1, max_length=120)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    excerpt: str = Field(max_length=160)


class GuardrailResult(BaseModel):
    """Aggregated guardrail scan result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[GuardrailFinding, ...] = Field(default_factory=tuple)

    @computed_field
    @property
    def blocked(self) -> bool:
        return any(finding.severity == GuardrailSeverity.HIGH for finding in self.findings)


PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str], GuardrailSeverity], ...] = (
    (
        "ignore_instructions",
        re.compile(r"\b(ignore|override|forget|disregard)\b.{0,60}\b(instructions?|rules?|policy|system)\b", re.I),
        GuardrailSeverity.HIGH,
    ),
    (
        "reveal_system_prompt",
        re.compile(r"\b(show|reveal|print|dump|exfiltrate)\b.{0,80}\b(system prompt|developer message|hidden instructions?)\b", re.I),
        GuardrailSeverity.HIGH,
    ),
    (
        "jailbreak",
        re.compile(r"\b(jailbreak|dan mode|developer mode|bypass safety|unrestricted mode)\b", re.I),
        GuardrailSeverity.HIGH,
    ),
    (
        "tool_exfiltration",
        re.compile(r"\b(send|post|upload|email)\b.{0,80}\b(secrets?|tokens?|credentials?|api keys?)\b", re.I),
        GuardrailSeverity.HIGH,
    ),
)

PII_PATTERNS: tuple[tuple[str, re.Pattern[str], GuardrailSeverity], ...] = (
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
        GuardrailSeverity.MEDIUM,
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}(?!\d)"),
        GuardrailSeverity.MEDIUM,
    ),
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        GuardrailSeverity.HIGH,
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        GuardrailSeverity.HIGH,
    ),
)


def _excerpt(text: str, start: int, end: int) -> str:
    prefix_start = max(0, start - 20)
    suffix_end = min(len(text), end + 20)
    return text[prefix_start:suffix_end].replace("\n", " ")


def scan_prompt(text: str) -> GuardrailResult:
    """Scan text for prompt injection and PII signals."""

    findings: list[GuardrailFinding] = []
    for label, pattern, severity in PROMPT_INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.PROMPT_INJECTION,
                    severity=severity,
                    label=label,
                    start=match.start(),
                    end=match.end(),
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )

    for label, pattern, severity in PII_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.PII,
                    severity=severity,
                    label=label,
                    start=match.start(),
                    end=match.end(),
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )

    return GuardrailResult(findings=tuple(findings))


def redact_pii(text: str, replacement: str = "[REDACTED]") -> str:
    """Redact simple PII patterns from text."""

    redacted = text
    for _, pattern, _ in PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def detect_prompt_injection(text: str) -> bool:
    """Return True when text contains prompt-injection indicators."""

    return any(finding.category == GuardrailCategory.PROMPT_INJECTION for finding in scan_prompt(text).findings)


def detect_pii(text: str) -> bool:
    """Return True when text contains simple PII indicators."""

    return any(finding.category == GuardrailCategory.PII for finding in scan_prompt(text).findings)


def assert_prompt_safe(text: str) -> GuardrailResult:
    """Return scan result or raise an AssistantError for blocking findings."""

    result = scan_prompt(text)
    if result.blocked:
        categories = sorted({finding.category.value for finding in result.findings})
        code = (
            ErrorCode.PROMPT_INJECTION_DETECTED
            if GuardrailCategory.PROMPT_INJECTION.value in categories
            else ErrorCode.PII_DETECTED
        )
        raise AssistantError(
            code,
            "prompt failed guardrail checks",
            context={
                "categories": categories,
                "findings": [finding.model_dump(mode="json") for finding in result.findings],
            },
        )
    return result
