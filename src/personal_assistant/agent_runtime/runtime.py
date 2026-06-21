"""Small local runtime used by tests and early CLI/API adapters."""

from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.agent_runtime.models import AgentResult, AgentStatus
from personal_assistant.shared.guardrails import assert_prompt_safe
from personal_assistant.shared.schemas import Principal, TokenBudget
from personal_assistant.shared.tracing import TraceEvent, TraceEventType, TraceRecorder


@dataclass(slots=True)
class LocalAgentRuntime:
    agent_id: str = "personal_assistant"
    traces: TraceRecorder | None = None

    def run(self, task: str, *, principal: Principal, budget: TokenBudget) -> AgentResult:
        recorder = self.traces or TraceRecorder()
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

