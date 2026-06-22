"""Small local runtime used by tests and early CLI/API adapters."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AgentResult, AgentStatus
from personal_assistant.application.ports.observability import TraceRecorderPort
from personal_assistant.domain.common.guardrails import assert_prompt_safe
from personal_assistant.domain.common.identity import Principal
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType


class NullTraceRecorder:
    """No-op trace sink used when local runtime callers do not inject observability."""

    def write(self, event: TraceEvent) -> None:
        return None


@dataclass(slots=True)
class LocalAgentRuntime:
    agent_id: str = "personal_assistant"
    traces: TraceRecorderPort | None = None

    def run(self, task: str, *, principal: Principal, budget: TokenBudget) -> AgentResult:
        recorder = self.traces or NullTraceRecorder()
        started = TraceEvent(
            agent_id=self.agent_id,
            event_type=TraceEventType.agent_started,
            tenant_id=principal.tenant_id,
            input_summary={"task": task[:120]},
        )
        recorder.write(started)
        assert_prompt_safe(task)
        completed = TraceEvent(
            run_id=started.run_id,
            agent_id=self.agent_id,
            event_type=TraceEventType.agent_completed,
            tenant_id=principal.tenant_id,
            output_summary={"status": AgentStatus.completed.value},
        )
        recorder.write(completed)
        return AgentResult(
            run_id=started.run_id,
            agent_id=self.agent_id,
            status=AgentStatus.completed,
            tenant_id=principal.tenant_id,
            reply="Solicitud recibida.",
            trace_ids=[started.trace_id, completed.trace_id],
        )
