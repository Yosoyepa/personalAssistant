"""Simple local guardrails for prompt injection and PII detection."""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode


class GuardrailCategory(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    PII = "pii"
    CONTENT_POLICY = "content_policy"


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocked(self) -> bool:
        return any(finding.severity == GuardrailSeverity.HIGH for finding in self.findings)


PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str], GuardrailSeverity], ...] = (
    (
        "ignore_instructions",
        re.compile(r"\b(ignore|override|forget|disregard)\b.{0,60}\b(instructions?|rules?|policy|system)\b", re.IGNORECASE),
        GuardrailSeverity.HIGH,
    ),
    (
        "reveal_system_prompt",
        re.compile(r"\b(show|reveal|print|dump|exfiltrate)\b.{0,80}\b(system prompt|developer message|hidden instructions?)\b", re.IGNORECASE),
        GuardrailSeverity.HIGH,
    ),
    (
        "jailbreak",
        re.compile(r"\b(jailbreak|dan mode|developer mode|bypass safety|unrestricted mode)\b", re.IGNORECASE),
        GuardrailSeverity.HIGH,
    ),
    (
        "tool_exfiltration",
        re.compile(r"\b(send|post|upload|email)\b.{0,80}\b(secrets?|tokens?|credentials?|api keys?)\b", re.IGNORECASE),
        GuardrailSeverity.HIGH,
    ),
)

PII_PATTERNS: tuple[tuple[str, re.Pattern[str], GuardrailSeverity], ...] = (
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
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

# Content-policy rules ratified in docs/policy/content-policy.md.
# Input rules are born as flag (MEDIUM); they surface abuse signals in scan
# results without blocking legitimate reminders.
CONTENT_POLICY_INPUT_PATTERNS: tuple[tuple[str, re.Pattern[str], GuardrailSeverity], ...] = (
    (
        "cp_in_001_violent_threat",
        re.compile(
            r"\b(kill|murder|assassinate|bomb|hurt)\b.{0,40}"
            r"\b(you|him|her|them|someone|my\s+(?:boss|partner|neighbor|neighbour))\b",
            re.IGNORECASE,
        ),
        GuardrailSeverity.MEDIUM,
    ),
    (
        "cp_in_002_secret_sharing",
        re.compile(
            r"\b(password|passwd|api[_ -]?key|secret|token)\b\s*(?:is|=|:)\s*\S+",
            re.IGNORECASE,
        ),
        GuardrailSeverity.MEDIUM,
    ),
)

# Output rules protect the user and the operator from unsafe assistant replies.
# All four are explicit high-risk rules (block, HIGH); see the policy document
# for the per-rule rationale.
CONTENT_POLICY_OUTPUT_PATTERNS: tuple[tuple[str, re.Pattern[str], GuardrailSeverity], ...] = (
    (
        "cp_out_001_credential_material",
        re.compile(
            r"(?:\bsk-[A-Za-z0-9]{20,}\b"
            r"|\bAKIA[0-9A-Z]{16}\b"
            r"|\bghp_[A-Za-z0-9]{30,}\b"
            r"|\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
            r"|\b\d{8,10}:[A-Za-z0-9_-]{35}\b"
            r"|\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"
            r"|-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----)"
        ),
        GuardrailSeverity.HIGH,
    ),
    (
        "cp_out_002_exfiltration_instruction",
        re.compile(
            r"(?:\bexfiltrat\w*\b"
            r"|\b(?:send|post|upload|email|forward)\b.{0,80}\bhttps?://)",
            re.IGNORECASE,
        ),
        GuardrailSeverity.HIGH,
    ),
    (
        "cp_out_003_hidden_instruction_leak",
        re.compile(
            r"(?:\b(?:system prompt|developer message|hidden instructions?)\b\s*[:=]"
            r"|\bmy\s+(?:system prompt|hidden instructions?)\s+(?:is|are|says?)\b)",
            re.IGNORECASE,
        ),
        GuardrailSeverity.HIGH,
    ),
    (
        "cp_out_004_destructive_action",
        re.compile(
            r"(?:\brm\s+-[rf]{1,2}(?:\s|$)"
            r"|\bdel\s+/[fsq]\b"
            r"|\bformat\s+[a-z]:"
            r"|\bdrop\s+table\b"
            r"|\bdelete\s+all\s+(?:files|data|records)\b"
            r"|\bwipe\s+(?:the\s+)?(?:disk|drive|database)\b)",
            re.IGNORECASE,
        ),
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

    for label, pattern, severity in CONTENT_POLICY_INPUT_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.CONTENT_POLICY,
                    severity=severity,
                    label=label,
                    start=match.start(),
                    end=match.end(),
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )

    return GuardrailResult(findings=tuple(findings))


def scan_output(text: str) -> GuardrailResult:
    """Scan assistant output text for content-policy violations."""

    findings: list[GuardrailFinding] = []
    for label, pattern, severity in CONTENT_POLICY_OUTPUT_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.CONTENT_POLICY,
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


def assert_output_safe(text: str) -> GuardrailResult:
    """Return the output scan result or raise GuardrailViolation when blocked.

    The error context carries only categories, labels, and severities so that
    blocked-output diagnostics never echo raw user or assistant content.
    """

    result = scan_output(text)
    if result.blocked:
        raise GuardrailViolation(
            "assistant output failed content policy checks",
            context={
                "categories": sorted(
                    {finding.category.value for finding in result.findings}
                ),
                "findings": [
                    {
                        "category": finding.category.value,
                        "label": finding.label,
                        "severity": finding.severity.value,
                    }
                    for finding in result.findings
                ],
            },
        )
    return result
