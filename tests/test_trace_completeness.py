"""Fail-closed trace completeness contract and recorder enforcement."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import pytest

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.persistence.postgres import PostgresPersistence
from personal_assistant.application.dto.tracing import (
    REQUIRED_TRACE_FIELDS,
    IncompleteTraceEventError,
    TraceEvent,
    TraceEventType,
    require_trace_completeness,
)
from personal_assistant.evals.runner import run_suite
from personal_assistant.infrastructure.migrations import apply_migrations

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUITE = REPOSITORY_ROOT / "eval" / "cases"

#: Minimal privacy-safe payloads that make each event type complete.
_COMPLETE_FIELDS: dict[TraceEventType, dict[str, object]] = {
    TraceEventType.agent_started: {"input_summary": {"channel": "telegram"}},
    TraceEventType.context_selected: {"context_refs": ["agent_contract"]},
    TraceEventType.llm_called: {"model": "eval-model"},
    TraceEventType.tool_called: {"tool_call": {"name": "calendar.create_event"}},
    TraceEventType.guardrail_checked: {"validation": {"status": "passed"}},
    TraceEventType.approval_requested: {
        "tool_call": {"name": "calendar.create_event", "tier": "P3"}
    },
    TraceEventType.agent_completed: {"output_summary": {"status": "completed"}},
    TraceEventType.agent_failed: {"error": {"type": "EvalError"}},
}

_EMPTY_VALUES: dict[str, object] = {
    "input_summary": {},
    "context_refs": [],
    "model": None,
    "tool_call": {},
    "validation": {},
    "output_summary": {},
    "error": {},
}


def _event(event_type: TraceEventType) -> TraceEvent:
    return TraceEvent(
        agent_id="personal_assistant",
        event_type=event_type,
        tenant_id="tenant-trace-tests",
        **_COMPLETE_FIELDS[event_type],
    )


def test_contract_covers_every_event_type_exactly_once() -> None:
    assert set(REQUIRED_TRACE_FIELDS) == set(TraceEventType)
    assert all(REQUIRED_TRACE_FIELDS[event_type] for event_type in TraceEventType)


@pytest.mark.parametrize("event_type", list(TraceEventType))
def test_complete_events_are_accepted(event_type: TraceEventType) -> None:
    event = _event(event_type)
    assert require_trace_completeness(event) is event

    recorder = TraceRecorder()
    recorder.write(event)

    [stored] = recorder.list_for_tenant("tenant-trace-tests")
    assert stored.event_type == event_type


@pytest.mark.parametrize("event_type", list(TraceEventType))
def test_incomplete_events_are_rejected_fail_closed(event_type: TraceEventType) -> None:
    for field_name in REQUIRED_TRACE_FIELDS[event_type]:
        incomplete = _event(event_type)
        setattr(incomplete, field_name, _EMPTY_VALUES[field_name])
        with pytest.raises(IncompleteTraceEventError) as raised:
            require_trace_completeness(incomplete)
        assert event_type.value in str(raised.value)
        assert field_name in str(raised.value)

        recorder = TraceRecorder()
        with pytest.raises(IncompleteTraceEventError):
            recorder.write(incomplete)
        assert recorder.list_for_tenant("tenant-trace-tests") == []


@dataclass(frozen=True, slots=True)
class PostgresSandbox:
    dsn: str
    schema: str


@pytest.fixture
def trace_postgres() -> Iterator[PostgresSandbox]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL trace tests")
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    schema = f"p6_a2_{secrets.token_hex(6)}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        apply_migrations(dsn=dsn, schema=schema)
        yield PostgresSandbox(dsn=dsn, schema=schema)
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def _trace_row_count(sandbox: PostgresSandbox) -> int:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    with psycopg.connect(sandbox.dsn) as connection:
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM {}.assistant_trace_events").format(
                sql.Identifier(sandbox.schema)
            )
        ).fetchone()
    return int(row[0]) if row else -1


def test_postgres_recorder_rejects_incomplete_event_without_writing(
    trace_postgres: PostgresSandbox,
) -> None:
    recorder = PostgresPersistence(
        dsn=trace_postgres.dsn, schema=trace_postgres.schema
    ).traces
    complete = _event(TraceEventType.agent_completed)
    recorder.write(complete)
    assert _trace_row_count(trace_postgres) == 1

    incomplete = _event(TraceEventType.agent_completed)
    incomplete.output_summary = {}
    with pytest.raises(IncompleteTraceEventError):
        recorder.write(incomplete)

    assert _trace_row_count(trace_postgres) == 1


def test_trace_completeness_eval_cases_pass() -> None:
    result = run_suite(SUITE, categories=["trace-completeness"])

    assert result.selected == 2
    assert result.passed == 2
    assert result.failed == 0


def test_trace_completeness_eval_blocks_emission_regression(tmp_path: Path) -> None:
    suite = _write_regression_suite(tmp_path)

    result = run_suite(suite)

    assert result.failed == 1
    assert result.results[0].errors == ("output mismatch",)


def _write_regression_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(parents=True)
    case = {
        "id": "golden-trace-completeness-regression",
        "category": "trace-completeness",
        "tier": "golden",
        "failureMode": "complete-reminder-trace-emitted",
        "contractRefs": ["AUDIT-GAP-12"],
        "executor": "trace.completeness.v1",
        "input": {
            "scenario": "reminder-workflow",
            "eventType": None,
            "omittedField": None,
        },
        "expected": {
            "requiredTraceEvents": ["agent.started", "agent.completed"],
            "requiredTraceFields": {"agent.completed": ["output_summary"]},
            "incompleteEventRejected": None,
            "persistedEvents": 2,
        },
    }
    (suite / "cases.json").write_text(
        json.dumps({"schemaVersion": 1, "cases": [case]}), encoding="utf-8"
    )
    (suite / "suite.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "suiteId": "trace-regression-tests",
                "caseFiles": ["cases.json"],
            }
        ),
        encoding="utf-8",
    )
    return suite
