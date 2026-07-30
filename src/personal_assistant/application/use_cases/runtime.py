"""Small local runtime used by tests and early CLI/API adapters."""

from __future__ import annotations

from dataclasses import dataclass, field

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AgentResult, AgentStatus
from personal_assistant.application.dto.tracing import (
    GuardrailAction,
    TraceEvent,
    TraceEventType,
    build_guardrail_validation,
)
from personal_assistant.application.ports.observability import (
    TraceRecorderPort,
    emit_guardrail_checked,
)
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.guardrails import (
    GuardrailCategory,
    GuardrailResult,
    GuardrailViolation,
    scan_output,
    scan_prompt,
)
from personal_assistant.domain.common.identity import Principal


class NullTraceRecorder:
    """No-op trace sink used when local runtime callers do not inject observability."""

    def write(self, event: TraceEvent) -> None:
        return None

    def list_for_tenant(self, principal: Principal) -> list[TraceEvent]:
        return []

    def list_for_run(self, principal: Principal, run_id: str) -> list[TraceEvent]:
        return []


def derive_guardrail_action(result: GuardrailResult) -> GuardrailAction:
    """Reduce one scan result to its telemetry action.

    ``blocked`` when any finding is blocking, ``flagged`` when findings exist
    but none block, and ``allowed`` when the scan found nothing. Every scan
    point derives the action from the result itself so the emitted
    ``guardrail.checked`` event stays coherent with per-category attribution.
    """

    if result.blocked:
        return "blocked"
    if result.findings:
        return "flagged"
    return "allowed"


def enforce_prompt_scan(result: GuardrailResult) -> None:
    """Raise exactly as ``assert_prompt_safe`` would, without a second scan.

    Scan points emit ``guardrail.checked`` before enforcing, so the blocking
    error must be derived from the already-computed result instead of calling
    ``assert_prompt_safe`` (which would scan the same text twice).
    """

    if not result.blocked:
        return
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
            "findings": [
                finding.model_dump(mode="json") for finding in result.findings
            ],
        },
    )


def enforce_output_scan(result: GuardrailResult) -> None:
    """Raise exactly as ``assert_output_safe`` would, without a second scan.

    The error context carries only categories, labels, and severities so that
    blocked-output diagnostics never echo raw user or assistant content.
    """

    if not result.blocked:
        return
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


def emit_guardrail_scan(
    recorder: TraceRecorderPort,
    result: GuardrailResult,
    *,
    agent_id: str,
    tenant_id: str,
    run_id: str,
) -> TraceEvent:
    """Emit one sanitized ``guardrail.checked`` event for a scan result.

    Shared by every wired scan point (input and output) so the action
    derivation, payload sanitization, and fail-closed completeness checks are
    identical everywhere.
    """

    return emit_guardrail_checked(
        recorder,
        agent_id=agent_id,
        tenant_id=tenant_id,
        validation=build_guardrail_validation(result, derive_guardrail_action(result)),
        run_id=run_id,
    )


@dataclass(slots=True)
class LocalAgentRuntime:
    agent_id: str = "personal_assistant"
    traces: TraceRecorderPort | None = None
    replies: AssistantReplies = field(default_factory=AssistantReplies)

    def run(self, task: str, *, principal: Principal, budget: TokenBudget) -> AgentResult:
        recorder = self.traces or NullTraceRecorder()
        started = TraceEvent(
            agent_id=self.agent_id,
            event_type=TraceEventType.agent_started,
            tenant_id=principal.tenant_id,
            # Allowlisted diagnostic keys only: free-form task text would be
            # stripped by trace privacy redaction, leaving the required
            # input_summary empty and failing the fail-closed write contract.
            input_summary={"channel": "local", "text_length": len(task)},
        )
        recorder.write(started)
        # Scan -> emit -> enforce: the guardrail.checked event is written even
        # when the scan blocks, and enforcement reuses the same result so the
        # text is never scanned twice.
        input_scan = scan_prompt(task)
        emit_guardrail_scan(
            recorder,
            input_scan,
            agent_id=self.agent_id,
            tenant_id=principal.tenant_id,
            run_id=started.run_id,
        )
        enforce_prompt_scan(input_scan)
        completed = TraceEvent(
            run_id=started.run_id,
            agent_id=self.agent_id,
            event_type=TraceEventType.agent_completed,
            tenant_id=principal.tenant_id,
            output_summary={"status": AgentStatus.completed.value},
        )
        recorder.write(completed)
        reply = self.replies.runtime_request_received()
        output_scan = scan_output(reply)
        emit_guardrail_scan(
            recorder,
            output_scan,
            agent_id=self.agent_id,
            tenant_id=principal.tenant_id,
            run_id=started.run_id,
        )
        enforce_output_scan(output_scan)
        return AgentResult(
            run_id=started.run_id,
            agent_id=self.agent_id,
            status=AgentStatus.completed,
            tenant_id=principal.tenant_id,
            reply=reply,
            trace_ids=[started.trace_id, completed.trace_id],
        )
