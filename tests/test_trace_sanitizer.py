from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Self

from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.infrastructure import trace_sanitizer

RAW_RUN_ID = "command:telegram:918273645001:564738291002:intent"
RAW_CHAT_ID = "918273645001"


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rowcount = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self, statement: str, params: tuple[object, ...] | None = None
    ) -> None:
        self.connection.statements.append((statement, params))
        if statement.lstrip().startswith("UPDATE"):
            self.rowcount = 1

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self.connection.rows)


class _Connection:
    autocommit = True

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def transaction(self) -> nullcontext[None]:
        return nullcontext()


def _legacy_row() -> tuple[object, ...]:
    event = TraceEvent(
        trace_id="trace-legacy",
        run_id="run-placeholder",
        agent_id="personal_assistant",
        event_type=TraceEventType.agent_started,
        tenant_id="personal",
    )
    payload = event.model_dump(mode="json")
    payload["run_id"] = RAW_RUN_ID
    payload["input_summary"] = {
        "channel": "telegram",
        "chat_id": RAW_CHAT_ID,
        "message": "private historical text",
    }
    return (
        "personal",
        "trace-legacy",
        RAW_RUN_ID,
        "personal_assistant",
        "agent.started",
        event.timestamp,
        None,
        "0" * 64,
        payload,
    )


def test_dry_run_reports_changes_without_locking_or_writing() -> None:
    connection = _Connection([_legacy_row()])

    result = trace_sanitizer.sanitize_postgres_traces(connection=connection)

    assert result.dry_run is True
    assert result.scanned == 1
    assert result.changed == 1
    assert result.updated == 0
    assert len(connection.statements) == 1
    assert "FOR UPDATE" not in connection.statements[0][0]


def test_confirmed_apply_rewrites_in_place_without_raw_identifiers() -> None:
    connection = _Connection([_legacy_row()])

    result = trace_sanitizer.sanitize_postgres_traces(
        connection=connection, apply=True
    )

    assert result.updated == 1
    select, update = connection.statements
    assert "FOR UPDATE" in select[0]
    assert update[0].lstrip().startswith("UPDATE")
    assert "DELETE" not in update[0].upper()
    assert update[1] is not None
    serialized_params = json.dumps(update[1], default=str)
    assert RAW_RUN_ID not in serialized_params
    assert RAW_CHAT_ID not in serialized_params
    assert "private historical text" not in serialized_params
    rewritten_payload = json.loads(str(update[1][6]))
    assert rewritten_payload["run_id"].startswith("sha256:")
    assert rewritten_payload["input_summary"]["chat_id"].startswith("sha256:")
    assert rewritten_payload["input_summary"]["message"] == "[REDACTED]"


def test_cli_requires_literal_confirmation_before_loading_database(
    monkeypatch, capsys
) -> None:
    def _unexpected_settings_load() -> tuple[str | None, str]:
        raise AssertionError("database settings must not be loaded")

    monkeypatch.setattr(
        trace_sanitizer,
        "load_database_settings_from_env",
        _unexpected_settings_load,
    )

    assert trace_sanitizer.main(["--apply"]) == 2
    assert trace_sanitizer.main(["--confirm", trace_sanitizer.CONFIRMATION]) == 2
    captured = capsys.readouterr()
    assert RAW_CHAT_ID not in captured.out + captured.err
