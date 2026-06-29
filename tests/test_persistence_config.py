from __future__ import annotations

import builtins
import importlib
import importlib.util
import inspect
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

import personal_assistant.infrastructure.bootstrap as bootstrap
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.application.dto.events import CloudEvent, OutboxMessage
from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings


POSTGRES_DSN = "postgresql://assistant:secret@localhost:5432/assistant_test"
POSTGRES_MODULE = "personal_assistant.adapters.persistence.postgres"
PERSISTENCE_ENV_VARS = (
    "APP_ENV_FILE",
    "PERSISTENCE_BACKEND",
    "STORAGE_BACKEND",
    "DATABASE_BACKEND",
    "DATABASE_URL",
    "PERSISTENCE_DATABASE_URL",
    "POSTGRES_DSN",
)


def _settings_from_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> AppSettings:
    for name in PERSISTENCE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV_FILE", "disabled")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return AppSettings.from_env()


def _maybe_setting(settings: AppSettings, *names: str) -> Any:
    for name in names:
        if hasattr(settings, name):
            return getattr(settings, name)
    return None


def _setting_or_skip(settings: AppSettings, *names: str) -> Any:
    value = _maybe_setting(settings, *names)
    if value is None:
        pytest.skip(f"persistence config contract not present; missing one of: {', '.join(names)}")
    return value


def _normalized(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value).strip().lower().replace("-", "_")


def _message_text(exc: BaseException) -> str:
    pieces = [str(exc)]
    model_dump = getattr(exc, "model_dump", None)
    if callable(model_dump):
        pieces.append(json.dumps(model_dump(), sort_keys=True, default=str))
    return " ".join(pieces).lower()


def _assert_clear_error(
    exc: BaseException,
    *,
    mentions: tuple[tuple[str, ...], ...],
) -> None:
    text = _message_text(exc)
    for alternatives in mentions:
        assert any(term.lower() in text for term in alternatives), text


def _optional_module(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name or module_name.startswith(f"{exc.name}."):
            return None
        raise


def _persistence_builder() -> Callable[..., Any] | None:
    candidate_modules = (
        "personal_assistant.infrastructure.persistence",
        "personal_assistant.infrastructure.bootstrap",
    )
    candidate_names = (
        "build_persistence_backend",
        "build_persistence_adapters",
        "build_persistence",
        "build_storage_backend",
        "build_state_backend",
    )
    for module_name in candidate_modules:
        module = _optional_module(module_name)
        if module is None:
            continue
        for name in candidate_names:
            candidate = getattr(module, name, None)
            if callable(candidate):
                return candidate
    return None


def _call_with_settings_or_url(candidate: Callable[..., Any], settings: AppSettings) -> Any:
    signature = inspect.signature(candidate)
    kwargs: dict[str, Any] = {}
    args: list[Any] = []
    required_positional: list[inspect.Parameter] = []
    for name, parameter in signature.parameters.items():
        if name in {"self", "cls"}:
            continue
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if name == "settings":
            kwargs[name] = settings
        elif name in {"database_url", "dsn", "url", "connection_string"}:
            kwargs[name] = POSTGRES_DSN
        elif parameter.default is inspect.Parameter.empty and parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            required_positional.append(parameter)

    if required_positional and not kwargs:
        first = required_positional[0]
        if first.name in {"database_url", "dsn", "url", "connection_string"}:
            args.append(POSTGRES_DSN)
        else:
            args.append(settings)
    return candidate(*args, **kwargs)


def _call_with_settings_only(candidate: Callable[..., Any], settings: AppSettings) -> Any:
    signature = inspect.signature(candidate)
    parameters = signature.parameters
    if "settings" in parameters:
        return candidate(settings=settings)

    required_positional = [
        parameter
        for name, parameter in parameters.items()
        if name not in {"self", "cls"}
        and parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if required_positional:
        return candidate(settings)
    return candidate()


def _postgres_module_or_skip() -> Any:
    if importlib.util.find_spec(POSTGRES_MODULE) is None:
        pytest.skip("Postgres persistence adapter is not present in this fork")
    return importlib.import_module(POSTGRES_MODULE)


def _postgres_constructor(module: Any) -> Callable[..., Any] | None:
    for name in (
        "PostgresPersistenceBackend",
        "PostgresPersistenceAdapter",
        "PostgresAdapter",
        "PostgresStores",
        "PostgresEventStore",
        "PostgresOutbox",
        "PostgresWorkflowStateStore",
    ):
        candidate = getattr(module, name, None)
        if inspect.isclass(candidate):
            return candidate
    return None


def _clear_module(module_name: str) -> None:
    for loaded in list(sys.modules):
        if loaded == module_name or loaded.startswith(f"{module_name}."):
            del sys.modules[loaded]


def _block_import(monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
    original_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == module_name or name.startswith(f"{module_name}."):
            raise ModuleNotFoundError(f"No module named {module_name!r}", name=module_name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    _clear_module(module_name)


def _force_initialization(candidate: Any) -> None:
    for method_name in ("connect", "open", "ensure_schema", "initialize", "setup", "migrate"):
        method = getattr(candidate, method_name, None)
        if callable(method):
            method()
            return


def _sql_constants(module: Any) -> list[str]:
    statements: list[str] = []
    for value in vars(module).values():
        if not isinstance(value, str):
            continue
        compact = value.strip().lower()
        if re.search(r"\b(select|insert|update|delete|create table|alter table)\b", compact):
            statements.append(value)
    return statements


def _first_callable(module: Any, *names: str) -> Callable[..., Any] | None:
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


def _assert_json_column_serialized(record: Any, *candidate_keys: str) -> None:
    if not isinstance(record, dict):
        pytest.skip("serializer does not expose dict records")
    for key in candidate_keys:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, str):
            json.loads(value)
            return
        json.dumps(value)
        return
    pytest.fail(f"record lacks expected JSON payload key; keys={sorted(record)}")


def test_container_uses_in_memory_persistence_by_default() -> None:
    container = build_container()

    assert isinstance(container.calendar, LocalCalendarTool)
    assert isinstance(container.event_store, InMemoryEventStore)
    assert isinstance(container.outbox, InMemoryOutbox)
    assert isinstance(container.scheduler, ReminderScheduler)
    assert isinstance(container.states, InMemoryWorkflowStateStore)
    assert isinstance(container.traces, TraceRecorder)


def test_postgres_backend_factory_requires_complete_persistence_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    def fake_import(module_name: str) -> Any:
        assert module_name == POSTGRES_MODULE
        return SimpleNamespace(
            build_postgres_persistence=lambda *, database_url: SimpleNamespace(
                approvals=sentinel,
                calendar=sentinel,
                event_store=sentinel,
                memory=sentinel,
                outbox=sentinel,
                scheduler=sentinel,
                states=sentinel,
                traces=sentinel,
            )
        )

    monkeypatch.setattr(bootstrap, "import_module", fake_import)

    persistence = bootstrap.build_persistence_adapters(persistence_backend="postgres", database_url=POSTGRES_DSN)

    assert persistence.approvals is sentinel
    assert persistence.calendar is sentinel
    assert persistence.event_store is sentinel
    assert persistence.memory is sentinel
    assert persistence.outbox is sentinel
    assert persistence.scheduler is sentinel
    assert persistence.states is sentinel
    assert persistence.traces is sentinel


def test_persistence_backend_defaults_to_memory_when_config_contract_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_from_env(monkeypatch)

    backend = _setting_or_skip(settings, "persistence_backend", "storage_backend", "database_backend")

    assert _normalized(backend) in {"memory", "in_memory", "local", "ephemeral"}


def test_persistence_backend_selects_postgres_and_preserves_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_from_env(monkeypatch, PERSISTENCE_BACKEND="postgres", DATABASE_URL=POSTGRES_DSN)

    backend = _setting_or_skip(settings, "persistence_backend", "storage_backend", "database_backend")
    database_url = _setting_or_skip(settings, "database_url", "persistence_database_url", "postgres_dsn")

    assert _normalized(backend) == "postgres"
    assert database_url == POSTGRES_DSN


def test_postgres_backend_requires_database_url_with_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        settings = _settings_from_env(monkeypatch, PERSISTENCE_BACKEND="postgres")
    except Exception as exc:
        _assert_clear_error(
            exc,
            mentions=(("database_url", "database url", "DATABASE_URL"), ("postgres",)),
        )
        return

    backend = _setting_or_skip(settings, "persistence_backend", "storage_backend", "database_backend")
    if _normalized(backend) != "postgres":
        pytest.fail(f"PERSISTENCE_BACKEND=postgres was not honored; got {backend!r}")
    if _maybe_setting(settings, "database_url", "persistence_database_url", "postgres_dsn"):
        pytest.fail("postgres backend unexpectedly has a database URL when DATABASE_URL is absent")

    builder = _persistence_builder()
    if builder is None:
        pytest.fail("postgres backend is configurable, but no persistence backend factory was found")

    with pytest.raises(Exception) as ctx:
        _call_with_settings_only(builder, settings)

    _assert_clear_error(
        ctx.value,
        mentions=(("database_url", "database url", "DATABASE_URL"), ("postgres",)),
    )


def test_postgres_backend_reports_missing_psycopg_from_dynamic_import(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = getattr(bootstrap, "build_persistence_adapters", None)
    if not callable(builder):
        pytest.skip("persistence backend factory is not present in this fork")

    def missing_psycopg(module_name: str) -> Any:
        assert module_name == POSTGRES_MODULE
        raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")

    monkeypatch.setattr(bootstrap, "import_module", missing_psycopg)

    with pytest.raises(Exception) as ctx:
        builder(persistence_backend="postgres", database_url=POSTGRES_DSN)

    _assert_clear_error(
        ctx.value,
        mentions=(("postgres",), ("psycopg",), ("install", "optional", "dependency", "extra", "pip")),
    )


def test_postgres_backend_reports_clear_error_when_psycopg_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    if importlib.util.find_spec(POSTGRES_MODULE) is None:
        pytest.skip("Postgres persistence adapter is not present in this fork")

    _block_import(monkeypatch, "psycopg")
    _clear_module(POSTGRES_MODULE)
    try:
        module = importlib.import_module(POSTGRES_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg":
            pytest.fail("Postgres adapter imports psycopg at module import time instead of raising a clear setup error")
        raise

    settings = AppSettings()
    constructor = _postgres_constructor(module)
    builder = _persistence_builder()
    callable_under_test = constructor or builder
    if callable_under_test is None:
        pytest.skip("Postgres adapter has no discoverable constructor or backend factory")

    with pytest.raises(Exception) as ctx:
        instance = _call_with_settings_or_url(callable_under_test, settings)
        _force_initialization(instance)

    _assert_clear_error(
        ctx.value,
        mentions=(("psycopg",), ("install", "optional", "dependency", "extra", "pip")),
    )


def test_postgres_adapter_sql_is_parameterized_and_tenant_scoped() -> None:
    module = _postgres_module_or_skip()
    statements = _sql_constants(module)
    if not statements:
        pytest.skip("Postgres adapter does not expose module-level SQL statements")

    dml = [
        statement
        for statement in statements
        if re.search(r"\b(insert|update|delete|select)\b", statement, flags=re.IGNORECASE)
    ]
    assert dml, "expected at least one DML statement in the Postgres adapter"
    assert all("tenant_id" in statement.lower() for statement in dml)
    assert any("idempotency_key" in statement.lower() for statement in dml)

    for statement in dml:
        assert re.search(r"(%s|%\([^)]+\)s|\$[0-9]+|:[a-zA-Z_][a-zA-Z0-9_]*|\?)", statement), statement
        assert not re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", statement), statement


def test_postgres_adapter_serializes_contract_dtos_without_pydantic_objects() -> None:
    module = _postgres_module_or_skip()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    event = CloudEvent(
        id="evt_1",
        type="reminder.created",
        source="tests",
        tenant_id="tenant-a",
        data={"reminder_id": "rem_1", "at": "2026-01-02T03:04:00+00:00"},
        time=now,
    )
    outbox = OutboxMessage(
        id="out_1",
        tenant_id="tenant-a",
        event=event,
        idempotency_key="idem_1",
        created_at=now,
    )
    state = WorkflowState(
        workflow_id="wf_1",
        tenant_id="tenant-a",
        workflow_type="reminder.create",
        status=WorkflowStatus.completed,
        step="completed",
        idempotency_key="idem_1",
        data={"result": {"event_id": "evt_1"}},
        created_at=now,
        updated_at=now,
    )

    serializers = (
        (
            _first_callable(module, "_cloud_event_to_record", "cloud_event_to_record", "_serialize_cloud_event"),
            event,
            ("data", "payload"),
        ),
        (
            _first_callable(module, "_outbox_message_to_record", "outbox_message_to_record", "_serialize_outbox_message"),
            outbox,
            ("event", "payload"),
        ),
        (
            _first_callable(module, "_workflow_state_to_record", "workflow_state_to_record", "_serialize_workflow_state"),
            state,
            ("data", "payload", "state"),
        ),
    )
    exercised = 0

    for serializer, dto, json_keys in serializers:
        if serializer is None:
            continue
        record = serializer(dto)
        exercised += 1
        assert isinstance(record, dict)
        assert record.get("tenant_id") == "tenant-a"
        assert not any(hasattr(value, "model_dump") for value in record.values())
        assert not any(isinstance(value, Enum) for value in record.values())
        _assert_json_column_serialized(record, *json_keys)

    if exercised == 0:
        pytest.skip("Postgres adapter does not expose record serializer helpers")
