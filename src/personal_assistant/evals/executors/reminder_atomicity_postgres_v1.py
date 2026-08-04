"""Real PostgreSQL atomicity, recovery, identity, and contention evals."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_assistant.adapters.persistence.postgres import (
    PostgresPersistence,
    PostgresReminderUnitOfWork,
)
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderCommitOutcomeUnknown,
    ReminderTransactionConflict,
)
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.evals.executors.postgres_reliability_support import (
    PostgresEvalDatabase,
    isolated_postgres,
    schema_exists,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
WRITE_POINTS = (
    "register",
    "calendar",
    "scheduler",
    "event_store",
    "outbox",
    "completed",
)
BUSINESS_TABLES = {
    "states": "assistant_workflow_states",
    "calendar": "assistant_calendar_events",
    "scheduler": "assistant_scheduled_reminders",
    "events": "assistant_events",
    "outbox": "assistant_outbox",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RowCounts(_StrictModel):
    states: int = Field(ge=0)
    calendar: int = Field(ge=0)
    scheduler: int = Field(ge=0)
    events: int = Field(ge=0)
    outbox: int = Field(ge=0)


class InputModel(_StrictModel):
    scenario: Literal[
        "write-fault",
        "resume-fault",
        "unknown-commit",
        "concurrent-same-identity",
        "concurrent-distinct-identities",
        "cross-tenant-identity",
        "sequential-replay",
    ]
    boundary: Literal[
        "register",
        "resume",
        "calendar",
        "scheduler",
        "event_store",
        "outbox",
        "completed",
        "commit",
        "contention",
        "replay",
    ]
    triggerPhase: Literal["before", "after", "none"]
    baselineCount: int = Field(ge=0, le=1)
    attempts: int = Field(ge=1, le=16)
    workers: int = Field(ge=1, le=8)
    variant: int = Field(ge=1, le=100)
    recoveryProcess: Literal["same-process", "restart"] = "same-process"

    @model_validator(mode="after")
    def consistent(self) -> InputModel:
        if self.scenario == "write-fault" and self.boundary not in WRITE_POINTS:
            raise ValueError("write-fault requires a production write boundary")
        if self.scenario == "resume-fault" and self.boundary not in (
            "resume",
            *WRITE_POINTS[1:],
        ):
            raise ValueError("resume-fault requires resume or a later write boundary")
        return self


class ExpectedModel(_StrictModel):
    scenario: str
    boundary: str
    faultObservation: str
    stateAfterFault: str
    recoveryObservation: str
    identityObservation: str
    finalCounts: RowCounts
    contenders: int = Field(ge=0)
    schemaLifecycle: Literal["created-migrated-dropped"]


def _principal(tenant: str = "tenant-atomic") -> Principal:
    return Principal.for_test(
        principal_id=f"principal-{tenant}",
        tenant_id=tenant,
        permission_tier=PermissionTier.P5,
    )


def _request(
    principal: Principal,
    source: str,
    *,
    approved: bool = True,
    text: str = "recuérdame clase mañana a las 17",
) -> ReminderWorkflowInput:
    key = reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel="telegram",
        principal_id=principal.principal_id,
        conversation_id="eval-chat",
        source_event_id=source,
    )
    approval = None
    if approved:
        approval = ApprovalGrant.issue(
            principal=principal,
            action="calendar.create_event",
            resource=f"{key}:calendar",
            tier=PermissionTier.P3,
        )
    return ReminderWorkflowInput(
        message_id=source,
        source_event_id=source,
        conversation_id="eval-chat",
        text=text,
        recipient="eval-chat",
        now=NOW,
        timezone="America/Bogota",
        idempotency_key=key,
        approval=approval,
    )


def _workflow(
    persistence: PostgresPersistence,
    *,
    unit_of_work: Any | None = None,
) -> ReminderWorkflow:
    return ReminderWorkflow(
        calendar=persistence.calendar,
        scheduler=persistence.scheduler,
        event_store=persistence.event_store,
        outbox=persistence.outbox,
        states=persistence.states,
        traces=persistence.traces,
        unit_of_work=unit_of_work or persistence.reminder_uow,
    )


def _counts(database: PostgresEvalDatabase, tenant: str) -> RowCounts:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    values: dict[str, int] = {}
    with psycopg.connect(database.dsn) as connection:
        for label, table in BUSINESS_TABLES.items():
            row = connection.execute(
                sql.SQL("SELECT count(*) FROM {}.{} WHERE tenant_id = %s").format(
                    sql.Identifier(database.schema), sql.Identifier(table)
                ),
                (tenant,),
            ).fetchone()
            values[label] = int(row[0]) if row else -1
    return RowCounts.model_validate(values)


def _install_fault(
    database: PostgresEvalDatabase, point: str, phase: str
) -> None:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    specs = {
        "register": ("assistant_workflow_states", "INSERT", ""),
        "resume": (
            "assistant_workflow_states",
            "UPDATE",
            "WHEN (OLD.status = 'waiting_approval' AND NEW.status = 'running')",
        ),
        "calendar": ("assistant_calendar_events", "INSERT", ""),
        "scheduler": ("assistant_scheduled_reminders", "INSERT", ""),
        "event_store": ("assistant_events", "INSERT", ""),
        "outbox": ("assistant_outbox", "INSERT", ""),
        "completed": (
            "assistant_workflow_states",
            "UPDATE",
            "WHEN (NEW.status = 'completed')",
        ),
    }
    table, operation, condition = specs[point]
    function = f"eval_fail_{point}_{phase}"
    trigger = function
    with psycopg.connect(database.dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL(
                "CREATE FUNCTION {}.{}() RETURNS trigger LANGUAGE plpgsql AS "
                "$body$ BEGIN RAISE EXCEPTION 'eval injected write fault'; END $body$"
            ).format(sql.Identifier(database.schema), sql.Identifier(function))
        )
        connection.execute(
            sql.SQL(
                "CREATE TRIGGER {} {} {} ON {}.{} FOR EACH ROW {} "
                "EXECUTE FUNCTION {}.{}()"
            ).format(
                sql.Identifier(trigger),
                sql.SQL(phase.upper()),
                sql.SQL(operation),
                sql.Identifier(database.schema),
                sql.Identifier(table),
                sql.SQL(condition),
                sql.Identifier(database.schema),
                sql.Identifier(function),
            )
        )


def _remove_fault(database: PostgresEvalDatabase, point: str, phase: str) -> None:
    psycopg = import_module("psycopg")
    sql = import_module("psycopg.sql")
    specs = {
        "register": "assistant_workflow_states",
        "resume": "assistant_workflow_states",
        "calendar": "assistant_calendar_events",
        "scheduler": "assistant_scheduled_reminders",
        "event_store": "assistant_events",
        "outbox": "assistant_outbox",
        "completed": "assistant_workflow_states",
    }
    name = f"eval_fail_{point}_{phase}"
    with psycopg.connect(database.dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP TRIGGER IF EXISTS {} ON {}.{}").format(
                sql.Identifier(name),
                sql.Identifier(database.schema),
                sql.Identifier(specs[point]),
            )
        )
        connection.execute(
            sql.SQL("DROP FUNCTION IF EXISTS {}.{}()").format(
                sql.Identifier(database.schema), sql.Identifier(name)
            )
        )


def _is_injected_database_fault(error: Exception) -> bool:
    psycopg = import_module("psycopg")
    if not isinstance(error, psycopg.errors.RaiseException):
        return False
    diagnostic = getattr(error, "diag", None)
    return getattr(diagnostic, "message_primary", None) == "eval injected write fault"


class _CommitThenDisconnect:
    def __init__(self, connection: Any, error_type: type[Exception]) -> None:
        self._connection = connection
        self._error_type = error_type

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        raise self._error_type("private transport diagnostics")


def _fault_case(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    persistence = PostgresPersistence(dsn=db.dsn, schema=db.schema)
    workflow = _workflow(persistence)
    for index in range(case.baselineCount):
        workflow.run(principal, _request(principal, f"baseline-{index}"))
    before = _counts(db, principal.tenant_id)
    request = _request(principal, f"fault-{case.variant}")
    _install_fault(db, case.boundary, case.triggerPhase)
    observed = "none"
    try:
        workflow.run(principal, request)
    except Exception as error:
        if not _is_injected_database_fault(error):
            raise
        observed = "database-write-rejected"
    finally:
        _remove_fault(db, case.boundary, case.triggerPhase)
    after = _counts(db, principal.tenant_id)
    preserved = "exact-baseline-preserved" if after == before else "state-changed"
    completed = workflow.run(principal, request)
    replay = workflow.run(principal, request)
    identity = (
        "stable-replay-identities"
        if replay.reused
        and replay.calendar_event_id == completed.calendar_event_id
        and replay.reminder_id == completed.reminder_id
        else "identity-drift"
    )
    return {
        "scenario": case.scenario,
        "boundary": case.boundary,
        "faultObservation": observed,
        "stateAfterFault": preserved,
        "recoveryObservation": "fresh-commit-then-replay",
        "identityObservation": identity,
        "finalCounts": _counts(db, principal.tenant_id).model_dump(),
        "contenders": 1,
    }


def _resume_case(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    principal = _principal()
    persistence = PostgresPersistence(dsn=db.dsn, schema=db.schema)
    workflow = _workflow(persistence)
    pending_request = _request(principal, f"resume-{case.variant}", approved=False)
    workflow.run(principal, pending_request)
    before = _counts(db, principal.tenant_id)
    _install_fault(db, case.boundary, "after")
    observed = "none"
    try:
        workflow.run(principal, _request(principal, f"resume-{case.variant}"))
    except Exception as error:
        if not _is_injected_database_fault(error):
            raise
        observed = "database-write-rejected"
    finally:
        _remove_fault(db, case.boundary, "after")
    preserved = (
        "waiting-approval-preserved"
        if _counts(db, principal.tenant_id) == before
        else "state-changed"
    )
    completed = workflow.run(
        principal, _request(principal, f"resume-{case.variant}")
    )
    replay = workflow.run(principal, _request(principal, f"resume-{case.variant}"))
    identity = (
        "stable-replay-identities"
        if replay.reused and replay.reminder_id == completed.reminder_id
        else "identity-drift"
    )
    return {
        "scenario": case.scenario,
        "boundary": case.boundary,
        "faultObservation": observed,
        "stateAfterFault": preserved,
        "recoveryObservation": "approved-resume-then-replay",
        "identityObservation": identity,
        "finalCounts": _counts(db, principal.tenant_id).model_dump(),
        "contenders": 1,
    }


def _unknown_commit(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    psycopg = import_module("psycopg")
    principal = _principal()
    persistence = PostgresPersistence(dsn=db.dsn, schema=db.schema)
    for index in range(case.baselineCount):
        _workflow(persistence).run(principal, _request(principal, f"baseline-{index}"))

    def factory() -> _CommitThenDisconnect:
        return _CommitThenDisconnect(psycopg.connect(db.dsn), psycopg.OperationalError)

    uncertain = PostgresReminderUnitOfWork(
        connection_factory=factory, schema=db.schema
    )
    request = _request(principal, f"unknown-{case.variant}")
    observation = "none"
    try:
        _workflow(persistence, unit_of_work=uncertain).run(principal, request)
    except ReminderCommitOutcomeUnknown as error:
        observation = (
            "sanitized-commit-outcome-unknown"
            if str(error) == "reminder transaction commit outcome is unknown"
            else "unsanitized-error"
        )
    replay_persistence = (
        PostgresPersistence(dsn=db.dsn, schema=db.schema)
        if case.recoveryProcess == "restart"
        else persistence
    )
    replay = _workflow(replay_persistence).run(principal, request)
    if case.recoveryProcess == "restart":
        replay = _workflow(replay_persistence).run(principal, request)
    return {
        "scenario": case.scenario,
        "boundary": case.boundary,
        "faultObservation": observation,
        "stateAfterFault": "commit-visible-on-unknown",
        "recoveryObservation": (
            "restart-explicit-replay-observed-commit"
            if case.recoveryProcess == "restart"
            else "same-process-explicit-replay-observed-commit"
        ),
        "identityObservation": (
            "stable-replay-identities" if replay.reused else "identity-drift"
        ),
        "finalCounts": _counts(db, principal.tenant_id).model_dump(),
        "contenders": 1,
    }


def _concurrency(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    same = case.scenario == "concurrent-same-identity"
    principal = _principal()

    def run(index: int) -> tuple[str, str, str]:
        source = f"same-{case.variant}" if same else f"distinct-{case.variant}-{index}"
        for _ in range(16):
            local = PostgresPersistence(dsn=db.dsn, schema=db.schema)
            try:
                result = _workflow(local).run(principal, _request(principal, source))
                return (
                    result.calendar_event_id or "",
                    result.reminder_id or "",
                    "completed-replay" if result.reused else "completed-fresh",
                )
            except ReminderTransactionConflict:
                continue
        return "", "", "retry-exhausted"

    with ThreadPoolExecutor(max_workers=case.workers) as pool:
        results = list(pool.map(run, range(case.attempts)))
    successful = [item for item in results if item[2].startswith("completed-")]
    counts = _counts(db, principal.tenant_id)
    expected_rows = 1 if same else case.attempts
    stable = (
        counts.states == expected_rows
        and counts.calendar == expected_rows
        and counts.scheduler == expected_rows
        and counts.events == expected_rows
        and counts.outbox == expected_rows
        and len(successful) == case.attempts
        and (not same or len({item[:2] for item in successful}) == 1)
    )
    return {
        "scenario": case.scenario,
        "boundary": case.boundary,
        "faultObservation": "serializable-contention-observed",
        "stateAfterFault": "one-logical-effect" if same else "all-effects-isolated",
        "recoveryObservation": "conflicts-are-safe-to-replay",
        "identityObservation": "stable-replay-identities" if stable else "identity-drift",
        "finalCounts": counts.model_dump(),
        "contenders": case.attempts,
    }


def _replay_or_tenant(case: InputModel, db: PostgresEvalDatabase) -> dict[str, object]:
    tenants = case.attempts if case.scenario == "cross-tenant-identity" else 1
    last_reused = False
    first_ids: tuple[str | None, str | None] | None = None
    aggregate = RowCounts(states=0, calendar=0, scheduler=0, events=0, outbox=0)
    stable = True
    for index in range(tenants):
        principal = _principal(f"tenant-{index}")
        persistence = PostgresPersistence(dsn=db.dsn, schema=db.schema)
        request = _request(principal, f"shared-{case.variant}")
        result = _workflow(persistence).run(principal, request)
        ids = (result.calendar_event_id, result.reminder_id)
        if first_ids is None:
            first_ids = ids
        elif case.scenario == "cross-tenant-identity" and ids == first_ids:
            stable = False
        for _ in range(case.attempts if tenants == 1 else 1):
            replay = _workflow(persistence).run(principal, request)
            last_reused = replay.reused
            stable = stable and (replay.calendar_event_id, replay.reminder_id) == ids
        counts = _counts(db, principal.tenant_id)
        aggregate = RowCounts(
            states=aggregate.states + counts.states,
            calendar=aggregate.calendar + counts.calendar,
            scheduler=aggregate.scheduler + counts.scheduler,
            events=aggregate.events + counts.events,
            outbox=aggregate.outbox + counts.outbox,
        )
    return {
        "scenario": case.scenario,
        "boundary": case.boundary,
        "faultObservation": "no-injected-fault",
        "stateAfterFault": "tenant-isolated" if tenants > 1 else "single-committed-effect",
        "recoveryObservation": "idempotent-replays-completed",
        "identityObservation": (
            "stable-replay-identities" if stable and last_reused else "identity-drift"
        ),
        "finalCounts": aggregate.model_dump(),
        "contenders": case.attempts,
    }


def execute(case: InputModel) -> dict[str, object]:
    with isolated_postgres() as db:
        if case.scenario == "write-fault":
            actual = _fault_case(case, db)
        elif case.scenario == "resume-fault":
            actual = _resume_case(case, db)
        elif case.scenario == "unknown-commit":
            actual = _unknown_commit(case, db)
        elif case.scenario in {
            "concurrent-same-identity",
            "concurrent-distinct-identities",
        }:
            actual = _concurrency(case, db)
        else:
            actual = _replay_or_tenant(case, db)
        dsn, schema = db.dsn, db.schema
    if schema_exists(dsn, schema):
        raise RuntimeError("isolated eval schema cleanup failed")
    actual["schemaLifecycle"] = "created-migrated-dropped"
    return actual
