from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.in_memory_uow import (
    InMemoryReminderUnitOfWork,
)
from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import LLMResult
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    llm_usage_metrics,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.privacy import redact_trace_mapping
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import (
    DEFAULT_LLM_CONTEXT_WINDOW_TOKENS,
    AppSettings,
)

PII_MARKERS = (
    "private-recipient@example.test",
    "body-private-marker",
)


def _result(*, input_tokens: int, output_tokens: int) -> LLMResult:
    return LLMResult(
        provider="fake",
        model="fake-model",
        data={},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_usage_metrics_computes_utilization_from_real_usage() -> None:
    metrics = llm_usage_metrics(
        _result(input_tokens=20, output_tokens=15), context_window_tokens=1000
    )

    assert metrics == {
        "input_tokens": 20,
        "output_tokens": 15,
        "context_utilization": 0.02,
    }


def test_usage_metrics_rounds_utilization_to_four_decimals() -> None:
    metrics = llm_usage_metrics(
        _result(input_tokens=1, output_tokens=1), context_window_tokens=3
    )

    assert metrics["context_utilization"] == 0.3333


def test_usage_metrics_omits_keys_when_provider_reports_no_usage() -> None:
    metrics = llm_usage_metrics(
        _result(input_tokens=0, output_tokens=0), context_window_tokens=1000
    )

    assert metrics == {}


def test_usage_metrics_omits_utilization_without_input_tokens() -> None:
    metrics = llm_usage_metrics(
        _result(input_tokens=0, output_tokens=7), context_window_tokens=1000
    )

    assert metrics == {"input_tokens": 0, "output_tokens": 7}


def test_context_window_tokens_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "128000")

    settings = AppSettings.from_env()

    assert settings.llm_context_window_tokens == 128000


def test_context_window_tokens_defaults_to_provider_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.delenv("LLM_CONTEXT_WINDOW_TOKENS", raising=False)

    settings = AppSettings.from_env()

    assert settings.llm_context_window_tokens == DEFAULT_LLM_CONTEXT_WINDOW_TOKENS


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_context_window_tokens_env_rejects_non_positive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", raw)

    with pytest.raises(ValueError, match="LLM_CONTEXT_WINDOW_TOKENS"):
        AppSettings.from_env()


def test_context_window_tokens_env_rejects_non_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "not-a-number")

    with pytest.raises(ValueError):
        AppSettings.from_env()


@pytest.mark.parametrize("invalid", [0, -1, "200000", 200000.5, True])
def test_context_window_tokens_construction_rejects_invalid(invalid: object) -> None:
    with pytest.raises(ValueError, match="LLM_CONTEXT_WINDOW_TOKENS"):
        AppSettings(llm_context_window_tokens=invalid)  # type: ignore[arg-type]


def test_context_window_tokens_is_repr_safe() -> None:
    rendered = repr(AppSettings(llm_context_window_tokens=128000))

    assert "llm_context_window_tokens=128000" in rendered


def test_token_metrics_survive_trace_redaction_without_pii() -> None:
    payload = {
        "input_tokens": 20,
        "output_tokens": 15,
        "context_utilization": 0.02,
        "text": "body-private-marker",
        "recipient": "private-recipient@example.test",
    }

    redacted = redact_trace_mapping(payload)

    assert redacted["input_tokens"] == 20
    assert redacted["output_tokens"] == 15
    assert redacted["context_utilization"] == 0.02
    serialized = json.dumps(redacted, sort_keys=True)
    assert all(marker not in serialized for marker in PII_MARKERS)


def test_persisted_llm_called_trace_keeps_metrics_and_drops_pii() -> None:
    trace = TraceEvent(
        run_id="run-private",
        agent_id="personal_assistant",
        event_type=TraceEventType.llm_called,
        tenant_id="tenant-a",
        input_summary={"text": "body-private-marker"},
        output_summary={
            "matched": True,
            "input_tokens": 20,
            "output_tokens": 15,
            "context_utilization": 0.02,
            "reminder_text": "body-private-marker",
        },
    )

    persisted = trace.for_persistence()
    serialized = persisted.model_dump_json()

    assert persisted.output_summary["input_tokens"] == 20
    assert persisted.output_summary["output_tokens"] == 15
    assert persisted.output_summary["context_utilization"] == 0.02
    assert all(marker not in serialized for marker in PII_MARKERS)


class _ReminderLLM:
    def __init__(self, *, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def complete(self, request, *, budget: TokenBudget) -> LLMResult:
        return LLMResult(
            provider="fake",
            model="fake-model",
            data={
                "is_reminder": True,
                "title": "almorzar con Ana",
                "starts_at": "2026-06-20T15:33:00+00:00",
                "confidence": 0.91,
            },
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class _IntentLLM:
    def complete(self, request, *, budget: TokenBudget) -> LLMResult:
        return LLMResult(
            provider="fake",
            model="fake-router",
            data={
                "kind": "reminder.create",
                "confidence": 0.94,
                "reminder_text": "recuérdame en 2 minutos pagar el arriendo",
            },
            input_tokens=12,
            output_tokens=8,
        )


def _reminder_workflow(
    *, llm: _ReminderLLM, traces: TraceRecorder
) -> ReminderWorkflow:
    calendar = LocalCalendarTool()
    scheduler = ReminderScheduler()
    event_store = InMemoryEventStore()
    outbox = InMemoryOutbox()
    states = InMemoryWorkflowStateStore()
    return ReminderWorkflow(
        calendar=calendar,
        scheduler=scheduler,
        event_store=event_store,
        outbox=outbox,
        states=states,
        traces=traces,
        unit_of_work=InMemoryReminderUnitOfWork(
            calendar=calendar,
            scheduler=scheduler,
            event_store=event_store,
            outbox=outbox,
            states=states,
        ),
        llm=llm,
        llm_context_window_tokens=1000,
    )


def _principal() -> Principal:
    return Principal.for_test(
        principal_id="user-1",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )


_LLM_FALLBACK_TEXT = "necesito que quede lo de almorzar con Ana a las tres treinta y tres"


def _llm_fallback_request() -> ReminderWorkflowInput:
    return ReminderWorkflowInput(
        message_id="llm-1",
        source_event_id="llm-1",
        conversation_id="chat-1",
        text=_LLM_FALLBACK_TEXT,
        recipient="chat-1",
        now=datetime(2026, 6, 20, 12, tzinfo=UTC),
        idempotency_key=None,
        approval=None,
    )


def _llm_called_traces(traces: TraceRecorder, tenant_id: str) -> list[TraceEvent]:
    return [
        event
        for event in traces.list_for_tenant(tenant_id)
        if event.event_type == TraceEventType.llm_called
    ]


def test_reminder_extraction_trace_records_token_metrics() -> None:
    principal = _principal()
    traces = TraceRecorder()
    workflow = _reminder_workflow(
        llm=_ReminderLLM(input_tokens=20, output_tokens=15), traces=traces
    )

    workflow.run(principal, _llm_fallback_request())

    [trace] = _llm_called_traces(traces, principal.tenant_id)
    assert trace.output_summary["input_tokens"] == 20
    assert trace.output_summary["output_tokens"] == 15
    assert trace.output_summary["context_utilization"] == 0.02


def test_reminder_extraction_trace_omits_metrics_without_usage() -> None:
    principal = _principal()
    traces = TraceRecorder()
    workflow = _reminder_workflow(
        llm=_ReminderLLM(input_tokens=0, output_tokens=0), traces=traces
    )

    workflow.run(principal, _llm_fallback_request())

    [trace] = _llm_called_traces(traces, principal.tenant_id)
    assert "input_tokens" not in trace.output_summary
    assert "output_tokens" not in trace.output_summary
    assert "context_utilization" not in trace.output_summary


def test_context_selected_trace_records_prompt_size_metrics() -> None:
    principal = _principal()
    traces = TraceRecorder()
    workflow = _reminder_workflow(
        llm=_ReminderLLM(input_tokens=20, output_tokens=15), traces=traces
    )

    workflow.run(principal, _llm_fallback_request())

    context_traces = [
        event
        for event in traces.list_for_tenant(principal.tenant_id)
        if event.event_type == TraceEventType.context_selected
    ]
    assert len(context_traces) == 1
    assert context_traces[0].input_summary["text_length"] == len(_LLM_FALLBACK_TEXT)
    assert context_traces[0].input_summary["estimated_tokens"] == max(
        len(_LLM_FALLBACK_TEXT) // 4, 1
    )


def test_intent_inference_trace_records_token_metrics() -> None:
    container = build_container(
        llm=_IntentLLM(), llm_context_window_tokens=1000
    )
    principal = _principal()

    container.commands.handle(
        principal,
        NormalizedMessage(
            channel=ChannelName.telegram,
            actor_id=principal.principal_id,
            conversation_id="chat-1",
            message_id="42",
            source_event_id="42",
            text="porfa avísame en 2 minutos pagar el arriendo",
        ),
        now=datetime(2026, 6, 20, 12, tzinfo=UTC),
        timezone="America/Bogota",
    )

    intent_traces = [
        event
        for event in container.traces.list_for_tenant(principal.tenant_id)
        if event.event_type == TraceEventType.llm_called
        and "schema" not in event.input_summary
    ]
    assert len(intent_traces) == 1
    assert intent_traces[0].output_summary["input_tokens"] == 12
    assert intent_traces[0].output_summary["output_tokens"] == 8
    assert intent_traces[0].output_summary["context_utilization"] == 0.012
