"""Optional Postgres-backed persistence adapters.

This module intentionally does not import psycopg at module import time. Local
and test installations can import these classes without installing Postgres
client dependencies; psycopg is only imported when a real DSN-backed connection
is opened.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import importlib
import json
import re
from typing import Any
from uuid import uuid4

from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
)
from personal_assistant.application.dto.events import (
    CloudEvent,
    OutboxMessage,
    OutboxStatus,
)
from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.application.dto.workflows import (
    WorkflowState,
    WorkflowStateRegistration,
    WorkflowStatus,
)
from personal_assistant.application.ports.calendar import (
    CalendarEventRequest,
    CalendarEventResult,
)
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionRequest,
    PermissionTier,
    require_approval,
    require_permission,
)
from personal_assistant.domain.memory.models import MemoryKind, MemoryRecord
from personal_assistant.domain.reminders.idempotency import (
    ReminderIdempotencyConflict,
    ReminderPayload,
)


ConnectionFactory = Callable[[], Any]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_TABLES = {
    "events": "assistant_events",
    "outbox": "assistant_outbox",
    "workflow_states": "assistant_workflow_states",
    "approvals": "assistant_approvals",
    "calendar_events": "assistant_calendar_events",
    "scheduled_reminders": "assistant_scheduled_reminders",
    "memory_records": "assistant_memory_records",
    "trace_events": "assistant_trace_events",
}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _approval_hash(approval: PendingApproval) -> str:
    return _fingerprint(
        {
            "approval_id": approval.approval_id,
            "tenant_id": approval.tenant_id,
            "principal_id": approval.principal_id,
            "action": approval.action,
            "resource": approval.resource,
            "tier": approval.tier,
            "workflow_kind": approval.workflow_kind,
            "idempotency_key": approval.idempotency_key,
            "message_id": approval.message_id,
            "source_event_id": approval.source_event_id,
            "conversation_id": approval.conversation_id,
            "channel": approval.channel,
            "recipient": approval.recipient,
            "request_text": approval.request_text,
            "timezone": approval.timezone,
            "payload_fingerprint": approval.payload_fingerprint,
        }
    )


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid Postgres identifier: {value!r}")
    return f'"{value}"'


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _payload_from_row(row: Any, key: str = "payload", index: int = 0) -> dict[str, Any]:
    payload = _row_value(row, key, index)
    if isinstance(payload, str):
        return json.loads(payload)
    return dict(payload)


def _upgrade_legacy_pending_approval(payload: dict[str, Any]) -> dict[str, Any]:
    """Upgrade persisted pre-P1-A4 approvals without relaxing new writes."""

    upgraded = dict(payload)
    if not upgraded.get("source_event_id"):
        upgraded["source_event_id"] = upgraded.get("message_id")
    if not upgraded.get("payload_fingerprint"):
        upgraded["payload_fingerprint"] = ReminderPayload(
            text=str(upgraded["request_text"]),
            recipient=str(upgraded["recipient"]),
            timezone=str(upgraded.get("timezone") or "America/Bogota"),
        ).fingerprint
    return upgraded


def _upgrade_legacy_scheduled_reminder(payload: dict[str, Any]) -> dict[str, Any]:
    """Read legacy jobs with deterministic, explicit compatibility metadata."""

    upgraded = dict(payload)
    key = str(upgraded["idempotency_key"])
    if not upgraded.get("source_event_id"):
        upgraded["source_event_id"] = f"legacy:{key}"
    # Pre-P1-A4 jobs persisted only an aware instant, not the user's IANA zone.
    # UTC is the sole honest fallback; all new writes persist the original zone.
    if not upgraded.get("timezone"):
        upgraded["timezone"] = "UTC"
    if not upgraded.get("payload_fingerprint"):
        upgraded["payload_fingerprint"] = _fingerprint(
            {
                "schema": "personal-assistant.legacy-scheduled-reminder",
                "calendar_event_id": upgraded.get("calendar_event_id"),
                "notify_at": upgraded.get("notify_at"),
                "channel": upgraded.get("channel"),
                "recipient": upgraded.get("recipient"),
                "body": upgraded.get("body"),
                "timezone": upgraded["timezone"],
            }
        )
    return upgraded


def _upgrade_legacy_calendar_result(
    payload: dict[str, Any], request_payload: dict[str, Any]
) -> dict[str, Any]:
    """Restore result metadata from the request persisted beside legacy rows."""

    upgraded = dict(payload)
    if not upgraded.get("timezone"):
        timezone = request_payload.get("timezone")
        if not timezone:
            raise ValueError("persisted calendar request is missing timezone")
        upgraded["timezone"] = timezone
    for field in ("source_event_id", "payload_fingerprint"):
        if not upgraded.get(field) and request_payload.get(field):
            upgraded[field] = request_payload[field]
    return upgraded


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg":
            raise RuntimeError(
                "psycopg is required for PERSISTENCE_BACKEND=postgres. "
                "Install the optional postgres dependency, for example: "
                "pip install 'personal-assistant[postgres]'."
            ) from exc
        raise


def _close(cursor: Any) -> None:
    close = getattr(cursor, "close", None)
    if callable(close):
        close()


def _commit(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if callable(commit):
        commit()


def _rollback(connection: Any) -> None:
    rollback = getattr(connection, "rollback", None)
    if callable(rollback):
        rollback()


@dataclass(slots=True)
class _PostgresDatabase:
    dsn: str | None = None
    connection_factory: ConnectionFactory | None = None
    connection: Any | None = None
    schema: str = "public"
    commit: bool = True

    def __post_init__(self) -> None:
        sources = [
            self.dsn is not None,
            self.connection_factory is not None,
            self.connection is not None,
        ]
        if sum(sources) > 1:
            raise ValueError(
                "provide only one of dsn, connection_factory, or connection"
            )
        self.schema = _quote_identifier(self.schema)

    def table(self, name: str) -> str:
        return f"{self.schema}.{_quote_identifier(_TABLES[name])}"

    @contextmanager
    def connect(self) -> Any:
        managed = False
        connection = self.connection
        if connection is None:
            managed = True
            if self.connection_factory is not None:
                connection = self.connection_factory()
            else:
                psycopg = _load_psycopg()
                connection = (
                    psycopg.connect(self.dsn)
                    if self.dsn is not None
                    else psycopg.connect()
                )
        try:
            yield connection
            if self.commit:
                _commit(connection)
        except Exception:
            if self.commit:
                _rollback(connection)
            raise
        finally:
            if managed:
                _close(connection)

    @contextmanager
    def cursor(self) -> Any:
        with self.connect() as connection:
            cursor_cm = connection.cursor()
            if hasattr(cursor_cm, "__enter__"):
                with cursor_cm as cursor:
                    yield cursor
            else:
                try:
                    yield cursor_cm
                finally:
                    _close(cursor_cm)

    def ensure_schema(self) -> None:
        statements = _schema_statements(self)
        with self.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


class _PostgresStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_factory: ConnectionFactory | None = None,
        connection: Any | None = None,
        schema: str = "public",
        _database: _PostgresDatabase | None = None,
    ) -> None:
        self._db = _database or _PostgresDatabase(
            dsn=dsn,
            connection_factory=connection_factory,
            connection=connection,
            schema=schema,
        )

    def ensure_schema(self) -> None:
        self._db.ensure_schema()

    def _table(self, name: str) -> str:
        return self._db.table(name)


def ensure_schema(
    *,
    dsn: str | None = None,
    connection_factory: ConnectionFactory | None = None,
    connection: Any | None = None,
    schema: str = "public",
) -> None:
    """Create all Postgres persistence tables if they do not already exist."""

    _PostgresDatabase(
        dsn=dsn,
        connection_factory=connection_factory,
        connection=connection,
        schema=schema,
    ).ensure_schema()


class PostgresEventStore(_PostgresStore):
    def append(self, principal: Principal, event: CloudEvent) -> CloudEvent:
        require_trusted_principal(principal)
        if event.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "event tenant mismatch",
                tenant_id=principal.tenant_id,
            )

        payload = event.model_dump(mode="json")
        event_fingerprint = _fingerprint(payload)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("events")}
                    (tenant_id, event_id, event_type, source, occurred_at, fingerprint, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, event_id) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    event.id,
                    event.type,
                    event.source,
                    event.time,
                    event_fingerprint,
                    _json(payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return CloudEvent.model_validate(_payload_from_row(row))

            cursor.execute(
                f"""
                SELECT payload, fingerprint
                FROM {self._table("events")}
                WHERE tenant_id = %s AND event_id = %s
                """,
                (principal.tenant_id, event.id),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "event append failed",
                    tenant_id=principal.tenant_id,
                )
            if _row_value(existing, "fingerprint", 1) != event_fingerprint:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "event idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return CloudEvent.model_validate(_payload_from_row(existing))

    def list_for_tenant(self, principal: Principal) -> list[CloudEvent]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("events")}
                WHERE tenant_id = %s
                ORDER BY occurred_at, event_id
                """,
                (principal.tenant_id,),
            )
            return [
                CloudEvent.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]


class PostgresOutbox(_PostgresStore):
    def add(
        self, principal: Principal, event: CloudEvent, *, idempotency_key: str
    ) -> OutboxMessage:
        require_trusted_principal(principal)
        if event.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "outbox tenant mismatch",
                tenant_id=principal.tenant_id,
            )

        event_payload = event.model_dump(mode="json")
        event_fingerprint = _fingerprint(event_payload)
        message = OutboxMessage(
            tenant_id=principal.tenant_id, event=event, idempotency_key=idempotency_key
        )
        message_payload = message.model_dump(mode="json")
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("outbox")}
                    (
                        tenant_id, idempotency_key, message_id, event_id, dispatch_status,
                        claim_token, claim_owner, claimed_until, next_attempt_at, attempts,
                        created_at, published_at, event_payload, fingerprint, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    idempotency_key,
                    message.id,
                    event.id,
                    message.dispatch_status.value,
                    message.claim_token,
                    message.claim_owner,
                    message.claimed_until,
                    message.next_attempt_at,
                    message.attempts,
                    message.created_at,
                    message.published_at,
                    _json(event_payload),
                    event_fingerprint,
                    _json(message_payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return OutboxMessage.model_validate(_payload_from_row(row))

            cursor.execute(
                f"""
                SELECT payload, fingerprint
                FROM {self._table("outbox")}
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (principal.tenant_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "outbox insert failed",
                    tenant_id=principal.tenant_id,
                )
            if _row_value(existing, "fingerprint", 1) != event_fingerprint:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "outbox idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return OutboxMessage.model_validate(_payload_from_row(existing))

    def claim(
        self,
        principal: Principal,
        limit: int = 10,
        *,
        owner: str = "local-worker",
        lease_seconds: int = 60,
    ) -> list[OutboxMessage]:
        require_trusted_principal(principal)
        now = datetime.now(UTC)
        claimed_until = now + timedelta(seconds=lease_seconds)
        claimed: list[OutboxMessage] = []
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT idempotency_key, payload
                FROM {self._table("outbox")}
                WHERE tenant_id = %s
                  AND dispatch_status <> %s
                  AND (claimed_until IS NULL OR claimed_until <= %s)
                  AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
                ORDER BY created_at, message_id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (principal.tenant_id, OutboxStatus.published.value, now, now, limit),
            )
            rows = cursor.fetchall()
            for row in rows:
                idempotency_key = _row_value(row, "idempotency_key", 0)
                message = OutboxMessage.model_validate(_payload_from_row(row, index=1))
                updated = message.model_copy(
                    update={
                        "dispatch_status": OutboxStatus.claimed,
                        "claim_token": f"claim_{uuid4().hex}",
                        "claim_owner": owner,
                        "claimed_until": claimed_until,
                        "attempts": message.attempts + 1,
                    }
                )
                self._update_outbox_payload(
                    cursor, principal.tenant_id, idempotency_key, updated
                )
                claimed.append(updated)
        return claimed

    def mark_published(
        self, principal: Principal, message_id: str, *, claim_token: str
    ) -> OutboxMessage:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            message = self._get_by_message_id(cursor, principal.tenant_id, message_id)
            if message is None:
                raise AssistantError(
                    ErrorCode.NOT_FOUND,
                    "outbox message not found",
                    tenant_id=principal.tenant_id,
                )
            if message.published:
                return message
            if not message.claimed or message.claim_token != claim_token:
                raise AssistantError(
                    ErrorCode.PERMISSION_DENIED,
                    "invalid outbox claim token",
                    tenant_id=principal.tenant_id,
                )
            updated = message.model_copy(
                update={
                    "dispatch_status": OutboxStatus.published,
                    "claim_token": None,
                    "claim_owner": None,
                    "claimed_until": None,
                    "published_at": datetime.now(UTC),
                }
            )
            self._update_outbox_payload(
                cursor, principal.tenant_id, updated.idempotency_key, updated
            )
            return updated

    def release(
        self, principal: Principal, message_id: str, *, claim_token: str
    ) -> OutboxMessage:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            message = self._get_by_message_id(cursor, principal.tenant_id, message_id)
            if message is None:
                raise AssistantError(
                    ErrorCode.NOT_FOUND,
                    "outbox message not found",
                    tenant_id=principal.tenant_id,
                )
            if message.claim_token != claim_token:
                raise AssistantError(
                    ErrorCode.PERMISSION_DENIED,
                    "invalid outbox claim token",
                    tenant_id=principal.tenant_id,
                )
            updated = message.model_copy(
                update={
                    "dispatch_status": OutboxStatus.pending,
                    "claim_token": None,
                    "claim_owner": None,
                    "claimed_until": None,
                }
            )
            self._update_outbox_payload(
                cursor, principal.tenant_id, updated.idempotency_key, updated
            )
            return updated

    def list_for_tenant(self, principal: Principal) -> list[OutboxMessage]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("outbox")}
                WHERE tenant_id = %s
                ORDER BY created_at, message_id
                """,
                (principal.tenant_id,),
            )
            return [
                OutboxMessage.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]

    def _get_by_message_id(
        self, cursor: Any, tenant_id: str, message_id: str
    ) -> OutboxMessage | None:
        cursor.execute(
            f"""
            SELECT payload
            FROM {self._table("outbox")}
            WHERE tenant_id = %s AND message_id = %s
            FOR UPDATE
            """,
            (tenant_id, message_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return OutboxMessage.model_validate(_payload_from_row(row))

    def _update_outbox_payload(
        self, cursor: Any, tenant_id: str, idempotency_key: str, message: OutboxMessage
    ) -> None:
        payload = message.model_dump(mode="json")
        cursor.execute(
            f"""
            UPDATE {self._table("outbox")}
            SET dispatch_status = %s,
                claim_token = %s,
                claim_owner = %s,
                claimed_until = %s,
                next_attempt_at = %s,
                attempts = %s,
                published_at = %s,
                payload = %s::jsonb
            WHERE tenant_id = %s AND idempotency_key = %s
            """,
            (
                message.dispatch_status.value,
                message.claim_token,
                message.claim_owner,
                message.claimed_until,
                message.next_attempt_at,
                message.attempts,
                message.published_at,
                _json(payload),
                tenant_id,
                idempotency_key,
            ),
        )


class PostgresWorkflowStateStore(_PostgresStore):
    def register_or_replay(
        self,
        principal: Principal,
        state: WorkflowState,
        *,
        resume_from_step: str | None = None,
    ) -> WorkflowStateRegistration:
        """Atomically insert one executor or return the matching persisted replay."""

        require_trusted_principal(principal)
        if state.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "workflow tenant mismatch",
                tenant_id=principal.tenant_id,
            )
        if state.payload_fingerprint is None:
            raise ValueError(
                "payload_fingerprint is required for workflow registration"
            )

        payload = state.model_dump(mode="json")
        state_fingerprint = _fingerprint(payload)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("workflow_states")}
                    (
                        tenant_id, idempotency_key, workflow_id, workflow_type, status,
                        step, created_at, updated_at, payload_fingerprint, fingerprint, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    state.idempotency_key,
                    state.workflow_id,
                    state.workflow_type,
                    state.status.value,
                    state.step,
                    state.created_at,
                    state.updated_at,
                    state.payload_fingerprint,
                    state_fingerprint,
                    _json(payload),
                ),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return WorkflowStateRegistration(
                    state=WorkflowState.model_validate(_payload_from_row(inserted)),
                    replayed=False,
                )

            cursor.execute(
                f"""
                SELECT payload, payload_fingerprint
                FROM {self._table("workflow_states")}
                WHERE tenant_id = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (principal.tenant_id, state.idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "workflow registration disappeared during replay",
                    tenant_id=principal.tenant_id,
                    retryable=True,
                )
            persisted = WorkflowState.model_validate(_payload_from_row(existing))
            stored_payload_fingerprint = _row_value(existing, "payload_fingerprint", 1)
            if stored_payload_fingerprint != state.payload_fingerprint:
                raise ReminderIdempotencyConflict(
                    tenant_id=principal.tenant_id,
                    idempotency_key=state.idempotency_key,
                )
            if persisted.workflow_type != state.workflow_type:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "workflow identity is immutable",
                    tenant_id=principal.tenant_id,
                )
            if (
                resume_from_step is not None
                and persisted.status == WorkflowStatus.waiting_approval
                and persisted.step == resume_from_step
            ):
                resumed = persisted.transition(status=WorkflowStatus.running)
                resumed_payload = resumed.model_dump(mode="json")
                cursor.execute(
                    f"""
                    UPDATE {self._table("workflow_states")}
                    SET status = %s,
                        updated_at = %s,
                        fingerprint = %s,
                        payload = %s::jsonb
                    WHERE tenant_id = %s
                      AND idempotency_key = %s
                      AND status = %s
                      AND step = %s
                      AND payload_fingerprint = %s
                    RETURNING payload
                    """,
                    (
                        resumed.status.value,
                        resumed.updated_at,
                        _fingerprint(resumed_payload),
                        _json(resumed_payload),
                        principal.tenant_id,
                        state.idempotency_key,
                        WorkflowStatus.waiting_approval.value,
                        resume_from_step,
                        state.payload_fingerprint,
                    ),
                )
                saved = cursor.fetchone()
                if saved is None:
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "workflow resume was not acquired",
                        tenant_id=principal.tenant_id,
                        retryable=True,
                    )
                return WorkflowStateRegistration(
                    state=WorkflowState.model_validate(_payload_from_row(saved)),
                    replayed=False,
                    resumed=True,
                )
            return WorkflowStateRegistration(state=persisted, replayed=True)

    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        require_trusted_principal(principal)
        if state.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "workflow tenant mismatch",
                tenant_id=principal.tenant_id,
            )

        payload = state.model_dump(mode="json")
        state_fingerprint = _fingerprint(payload)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload, fingerprint, status, payload_fingerprint
                FROM {self._table("workflow_states")}
                WHERE tenant_id = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (principal.tenant_id, state.idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                persisted = WorkflowState.model_validate(_payload_from_row(existing))
                stored_payload_fingerprint = _row_value(
                    existing, "payload_fingerprint", 3
                )
                if stored_payload_fingerprint != state.payload_fingerprint:
                    raise ReminderIdempotencyConflict(
                        tenant_id=principal.tenant_id,
                        idempotency_key=state.idempotency_key,
                    )
                if (
                    persisted.workflow_id != state.workflow_id
                    or persisted.workflow_type != state.workflow_type
                ):
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "workflow identity is immutable",
                        tenant_id=principal.tenant_id,
                    )
                status = WorkflowStatus(_row_value(existing, "status", 2))
                if status in {WorkflowStatus.completed, WorkflowStatus.failed}:
                    if _row_value(existing, "fingerprint", 1) != state_fingerprint:
                        raise AssistantError(
                            ErrorCode.CONFLICT,
                            "terminal workflow state is immutable",
                            tenant_id=principal.tenant_id,
                        )
                    return WorkflowState.model_validate(_payload_from_row(existing))

            cursor.execute(
                f"""
                INSERT INTO {self._table("workflow_states")}
                    (
                        tenant_id, idempotency_key, workflow_id, workflow_type, status,
                        step, created_at, updated_at, payload_fingerprint, fingerprint, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
                SET status = EXCLUDED.status,
                    step = EXCLUDED.step,
                    updated_at = EXCLUDED.updated_at,
                    fingerprint = EXCLUDED.fingerprint,
                    payload = EXCLUDED.payload
                WHERE {self._table("workflow_states")}.workflow_id = EXCLUDED.workflow_id
                  AND {self._table("workflow_states")}.workflow_type = EXCLUDED.workflow_type
                  AND {self._table("workflow_states")}.payload_fingerprint IS NOT DISTINCT FROM EXCLUDED.payload_fingerprint
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    state.idempotency_key,
                    state.workflow_id,
                    state.workflow_type,
                    state.status.value,
                    state.step,
                    state.created_at,
                    state.updated_at,
                    state.payload_fingerprint,
                    state_fingerprint,
                    _json(payload),
                ),
            )
            saved = cursor.fetchone()
            if saved is None:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "workflow identity is immutable",
                    tenant_id=principal.tenant_id,
                )
            return WorkflowState.model_validate(_payload_from_row(saved))

    def get_by_idempotency_key(
        self, principal: Principal, idempotency_key: str
    ) -> WorkflowState | None:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("workflow_states")}
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (principal.tenant_id, idempotency_key),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return WorkflowState.model_validate(_payload_from_row(row))

    def list_for_tenant(self, principal: Principal) -> list[WorkflowState]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("workflow_states")}
                WHERE tenant_id = %s
                ORDER BY created_at, workflow_id
                """,
                (principal.tenant_id,),
            )
            return [
                WorkflowState.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]


class PostgresApprovalStore(_PostgresStore):
    def create(
        self, principal: Principal, approval: PendingApproval
    ) -> PendingApproval:
        require_trusted_principal(principal)
        if (
            approval.tenant_id != principal.tenant_id
            or approval.principal_id != principal.principal_id
        ):
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "approval principal mismatch",
                tenant_id=principal.tenant_id,
            )
        tier = PermissionTier(approval.tier)
        if tier.rank < PermissionTier.P3.rank:
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                "approval requests are only valid for P3+ actions",
                tenant_id=principal.tenant_id,
            )

        payload = approval.model_dump(mode="json")
        approval_fingerprint = _approval_hash(approval)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("approvals")}
                    (
                        tenant_id, principal_id, approval_id, action, resource, tier,
                        workflow_kind, idempotency_key, status, created_at, updated_at,
                        fingerprint, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, principal_id, workflow_kind, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    principal.principal_id,
                    approval.approval_id,
                    approval.action,
                    approval.resource,
                    tier.value,
                    approval.workflow_kind,
                    approval.idempotency_key,
                    approval.status.value,
                    approval.created_at,
                    approval.updated_at,
                    approval_fingerprint,
                    _json(payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return PendingApproval.model_validate(
                    _upgrade_legacy_pending_approval(_payload_from_row(row))
                ).model_copy(deep=True)

            cursor.execute(
                f"""
                SELECT payload, fingerprint
                FROM {self._table("approvals")}
                WHERE tenant_id = %s
                  AND principal_id = %s
                  AND workflow_kind = %s
                  AND idempotency_key = %s
                """,
                (
                    principal.tenant_id,
                    principal.principal_id,
                    approval.workflow_kind,
                    approval.idempotency_key,
                ),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "approval insert failed",
                    tenant_id=principal.tenant_id,
                )
            existing_approval = PendingApproval.model_validate(
                _upgrade_legacy_pending_approval(_payload_from_row(existing))
            )
            if (
                _row_value(existing, "fingerprint", 1) != approval_fingerprint
                and _approval_hash(existing_approval) != approval_fingerprint
            ):
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "approval request idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return existing_approval.model_copy(deep=True)

    def get(self, principal: Principal, approval_id: str) -> PendingApproval | None:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("approvals")}
                WHERE tenant_id = %s AND principal_id = %s AND approval_id = %s
                """,
                (principal.tenant_id, principal.principal_id, approval_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return PendingApproval.model_validate(
                _upgrade_legacy_pending_approval(_payload_from_row(row))
            ).model_copy(deep=True)

    def list_pending(self, principal: Principal) -> list[PendingApproval]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("approvals")}
                WHERE tenant_id = %s AND principal_id = %s AND status = %s
                ORDER BY created_at, approval_id
                """,
                (
                    principal.tenant_id,
                    principal.principal_id,
                    PendingApprovalStatus.pending.value,
                ),
            )
            return [
                PendingApproval.model_validate(
                    _upgrade_legacy_pending_approval(_payload_from_row(row))
                ).model_copy(deep=True)
                for row in cursor.fetchall()
            ]

    def list_for_tenant(self, principal: Principal) -> list[PendingApproval]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("approvals")}
                WHERE tenant_id = %s AND principal_id = %s
                ORDER BY created_at, approval_id
                """,
                (principal.tenant_id, principal.principal_id),
            )
            return [
                PendingApproval.model_validate(
                    _upgrade_legacy_pending_approval(_payload_from_row(row))
                ).model_copy(deep=True)
                for row in cursor.fetchall()
            ]

    def mark_approved(self, principal: Principal, approval_id: str) -> PendingApproval:
        approval = self.get(principal, approval_id)
        if approval is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if approval.status != PendingApprovalStatus.pending:
            return approval
        updated = approval.model_copy(
            update={
                "status": PendingApprovalStatus.approved,
                "updated_at": datetime.now(UTC),
            }
        )
        self._update_status(principal, updated)
        return updated

    def approve(self, principal: Principal, approval_id: str) -> ApprovalGrant:
        approval = self.get(principal, approval_id)
        if approval is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if approval.status == PendingApprovalStatus.cancelled:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "approval was cancelled",
                tenant_id=principal.tenant_id,
            )
        tier = PermissionTier(approval.tier)
        require_permission(
            principal,
            PermissionRequest(
                action=approval.action, resource=approval.resource, required_tier=tier
            ),
        )
        if approval.status == PendingApprovalStatus.pending:
            approval = self.mark_approved(principal, approval_id)
        return ApprovalGrant.issue(
            principal=principal,
            action=approval.action,
            resource=approval.resource,
            tier=tier,
            approval_id=approval.approval_id,
            request_hash=_approval_hash(approval),
        )

    def cancel(self, principal: Principal, approval_id: str) -> PendingApproval:
        approval = self.get(principal, approval_id)
        if approval is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if approval.status == PendingApprovalStatus.approved:
            raise AssistantError(
                ErrorCode.CONFLICT,
                "approved approval cannot be cancelled",
                tenant_id=principal.tenant_id,
            )
        if approval.status == PendingApprovalStatus.cancelled:
            return approval
        updated = approval.model_copy(
            update={
                "status": PendingApprovalStatus.cancelled,
                "updated_at": datetime.now(UTC),
            }
        )
        self._update_status(principal, updated)
        return updated

    def reject(self, principal: Principal, approval_id: str) -> PendingApproval:
        return self.cancel(principal, approval_id)

    def _update_status(self, principal: Principal, approval: PendingApproval) -> None:
        payload = approval.model_dump(mode="json")
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {self._table("approvals")}
                SET status = %s,
                    updated_at = %s,
                    payload = %s::jsonb
                WHERE tenant_id = %s AND principal_id = %s AND approval_id = %s
                """,
                (
                    approval.status.value,
                    approval.updated_at,
                    _json(payload),
                    principal.tenant_id,
                    principal.principal_id,
                    approval.approval_id,
                ),
            )


class PostgresCalendarStore(_PostgresStore):
    """Postgres replacement for the local calendar adapter."""

    permission_tier = PermissionTier.P3

    def create_event(
        self,
        principal: Principal,
        request: CalendarEventRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> CalendarEventResult:
        require_approval(
            principal=principal,
            tier=self.permission_tier,
            approval=approval,
            action="calendar.create_event",
            resource=request.idempotency_key,
        )
        request_payload = request.model_dump(mode="json")
        request_fingerprint = _fingerprint(request_payload)
        result = CalendarEventResult(
            event_id=f"cal_{uuid4().hex}",
            title=request.title,
            starts_at=request.starts_at,
            timezone=request.timezone,
            idempotency_key=request.idempotency_key,
            source_event_id=request.source_event_id,
            payload_fingerprint=request.payload_fingerprint,
        )
        result_payload = result.model_dump(mode="json")
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("calendar_events")}
                    (
                        tenant_id, idempotency_key, event_id, title, starts_at,
                        request_fingerprint, request_payload, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    request.idempotency_key,
                    result.event_id,
                    request.title,
                    request.starts_at,
                    request_fingerprint,
                    _json(request_payload),
                    _json(result_payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return CalendarEventResult.model_validate(_payload_from_row(row))

            cursor.execute(
                f"""
                SELECT payload, request_fingerprint, request_payload
                FROM {self._table("calendar_events")}
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (principal.tenant_id, request.idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "calendar insert failed",
                    tenant_id=principal.tenant_id,
                )
            if _row_value(existing, "request_fingerprint", 1) != request_fingerprint:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "calendar idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return CalendarEventResult.model_validate(
                _upgrade_legacy_calendar_result(
                    _payload_from_row(existing),
                    _payload_from_row(existing, key="request_payload", index=2),
                )
            ).model_copy(update={"reused": True})

    def list_events(self, principal: Principal) -> list[CalendarEventResult]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload, request_payload
                FROM {self._table("calendar_events")}
                WHERE tenant_id = %s
                ORDER BY starts_at, event_id
                """,
                (principal.tenant_id,),
            )
            return [
                CalendarEventResult.model_validate(
                    _upgrade_legacy_calendar_result(
                        _payload_from_row(row),
                        _payload_from_row(row, key="request_payload", index=1),
                    )
                )
                for row in cursor.fetchall()
            ]


class PostgresReminderScheduler(_PostgresStore):
    """Postgres-backed reminder scheduler for local reminder jobs."""

    def schedule_before_event(
        self,
        principal: Principal,
        *,
        calendar_event_id: str,
        starts_at: datetime,
        channel: str,
        recipient: str,
        body: str,
        timezone: str,
        source_event_id: str,
        payload_fingerprint: str,
        minutes_before: int = 30,
        idempotency_key: str,
    ) -> ScheduledReminder:
        require_trusted_principal(principal)
        _require_aware(starts_at, "starts_at")
        notify_at = starts_at - timedelta(minutes=minutes_before)
        job = ScheduledReminder(
            tenant_id=principal.tenant_id,
            calendar_event_id=calendar_event_id,
            notify_at=notify_at,
            timezone=timezone,
            source_event_id=source_event_id,
            payload_fingerprint=payload_fingerprint,
            channel=channel,
            recipient=recipient,
            body=body,
            idempotency_key=idempotency_key,
        )
        payload = job.model_dump(mode="json")
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("scheduled_reminders")}
                    (
                        tenant_id, idempotency_key, reminder_id, calendar_event_id,
                        notify_at, channel, recipient, sent, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    idempotency_key,
                    job.reminder_id,
                    calendar_event_id,
                    notify_at,
                    channel,
                    recipient,
                    job.sent,
                    _json(payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return ScheduledReminder.model_validate(
                    _upgrade_legacy_scheduled_reminder(_payload_from_row(row))
                )

            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("scheduled_reminders")}
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (principal.tenant_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "reminder schedule failed",
                    tenant_id=principal.tenant_id,
                )
            stored = ScheduledReminder.model_validate(
                _upgrade_legacy_scheduled_reminder(_payload_from_row(existing))
            )
            if (
                stored.calendar_event_id != job.calendar_event_id
                or stored.notify_at != job.notify_at
                or stored.timezone != job.timezone
                or stored.source_event_id != job.source_event_id
                or stored.payload_fingerprint != job.payload_fingerprint
                or stored.channel != job.channel
                or stored.recipient != job.recipient
                or stored.body != job.body
            ):
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "reminder scheduler idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return stored

    def due(self, principal: Principal, now: datetime) -> list[ScheduledReminder]:
        require_trusted_principal(principal)
        _require_aware(now, "now")
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("scheduled_reminders")}
                WHERE tenant_id = %s AND sent = false AND notify_at <= %s
                ORDER BY notify_at, reminder_id
                """,
                (principal.tenant_id, now),
            )
            return [
                ScheduledReminder.model_validate(
                    _upgrade_legacy_scheduled_reminder(_payload_from_row(row))
                )
                for row in cursor.fetchall()
            ]

    def mark_sent(self, principal: Principal, reminder_id: str) -> ScheduledReminder:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT idempotency_key, payload
                FROM {self._table("scheduled_reminders")}
                WHERE tenant_id = %s AND reminder_id = %s
                FOR UPDATE
                """,
                (principal.tenant_id, reminder_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise AssistantError(
                    ErrorCode.NOT_FOUND,
                    "scheduled reminder not found",
                    tenant_id=principal.tenant_id,
                )
            idempotency_key = _row_value(row, "idempotency_key", 0)
            job = ScheduledReminder.model_validate(
                _upgrade_legacy_scheduled_reminder(_payload_from_row(row, index=1))
            ).model_copy(update={"sent": True})
            cursor.execute(
                f"""
                UPDATE {self._table("scheduled_reminders")}
                SET sent = true,
                    payload = %s::jsonb
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (
                    _json(job.model_dump(mode="json")),
                    principal.tenant_id,
                    idempotency_key,
                ),
            )
            return job

    def list_for_tenant(self, principal: Principal) -> list[ScheduledReminder]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("scheduled_reminders")}
                WHERE tenant_id = %s
                ORDER BY notify_at, reminder_id
                """,
                (principal.tenant_id,),
            )
            return [
                ScheduledReminder.model_validate(
                    _upgrade_legacy_scheduled_reminder(_payload_from_row(row))
                )
                for row in cursor.fetchall()
            ]


class PostgresMemoryStore(_PostgresStore):
    """Tenant and user scoped memory store backed by Postgres JSONB."""

    def add(
        self,
        principal: Principal,
        *,
        kind: MemoryKind,
        text: str,
        source: str,
        confirmed: bool = False,
    ) -> MemoryRecord:
        record = MemoryRecord(
            tenant_id=principal.tenant_id,
            user_id=principal.actor_id,
            kind=kind,
            text=text,
            source=source,
            confirmed=confirmed,
        )
        return self.save(record, principal=principal)

    def save(self, record: MemoryRecord, *, principal: Principal) -> MemoryRecord:
        require_trusted_principal(principal)
        if record.tenant_id != principal.tenant_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "memory tenant mismatch",
                tenant_id=principal.tenant_id,
            )
        if record.user_id is not None and record.user_id != principal.actor_id:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "memory user mismatch",
                tenant_id=principal.tenant_id,
            )
        if record.user_id is None:
            record = record.model_copy(update={"user_id": principal.actor_id})

        payload = record.model_dump(mode="json")
        record_fingerprint = _fingerprint(payload)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("memory_records")}
                    (
                        tenant_id, user_id, memory_id, kind, text, source,
                        confirmed, created_at, fingerprint, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, memory_id) DO NOTHING
                RETURNING payload
                """,
                (
                    principal.tenant_id,
                    record.user_id,
                    record.id,
                    record.kind.value,
                    record.text,
                    record.source,
                    record.confirmed,
                    record.created_at,
                    record_fingerprint,
                    _json(payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return MemoryRecord.model_validate(_payload_from_row(row))

            cursor.execute(
                f"""
                SELECT payload, fingerprint
                FROM {self._table("memory_records")}
                WHERE tenant_id = %s AND memory_id = %s
                """,
                (principal.tenant_id, record.id),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise AssistantError(
                    ErrorCode.INTERNAL_ERROR,
                    "memory insert failed",
                    tenant_id=principal.tenant_id,
                )
            if _row_value(existing, "fingerprint", 1) != record_fingerprint:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "memory idempotency conflict",
                    tenant_id=principal.tenant_id,
                )
            return MemoryRecord.model_validate(_payload_from_row(existing))

    def retrieve(
        self,
        principal: Principal,
        *,
        query: str,
        kind: MemoryKind | None = None,
        confirmed_only: bool = True,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        require_trusted_principal(principal)
        normalized = query.casefold()
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("memory_records")}
                WHERE tenant_id = %s
                  AND user_id = %s
                  AND (%s IS NULL OR kind = %s)
                  AND (%s = false OR confirmed = true)
                  AND (%s = '' OR POSITION(LOWER(%s) IN LOWER(text)) > 0)
                ORDER BY created_at, memory_id
                LIMIT %s
                """,
                (
                    principal.tenant_id,
                    principal.actor_id,
                    kind.value if kind is not None else None,
                    kind.value if kind is not None else None,
                    confirmed_only,
                    normalized,
                    normalized,
                    limit,
                ),
            )
            return [
                MemoryRecord.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]

    def list_for_tenant(self, principal: Principal) -> list[MemoryRecord]:
        require_trusted_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("memory_records")}
                WHERE tenant_id = %s AND user_id = %s
                ORDER BY created_at, memory_id
                """,
                (principal.tenant_id, principal.actor_id),
            )
            return [
                MemoryRecord.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]


class PostgresTraceRecorder(_PostgresStore):
    def write(self, event: TraceEvent) -> None:
        payload = event.model_dump(mode="json")
        trace_fingerprint = _fingerprint(payload)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self._table("trace_events")}
                    (
                        tenant_id, trace_id, run_id, agent_id, event_type,
                        timestamp, parent_event_id, fingerprint, payload
                    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, trace_id) DO NOTHING
                RETURNING payload
                """,
                (
                    event.tenant_id,
                    event.trace_id,
                    event.run_id,
                    event.agent_id,
                    event.event_type.value,
                    event.timestamp,
                    event.parent_event_id,
                    trace_fingerprint,
                    _json(payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return

            cursor.execute(
                f"""
                SELECT fingerprint
                FROM {self._table("trace_events")}
                WHERE tenant_id = %s AND trace_id = %s
                """,
                (event.tenant_id, event.trace_id),
            )
            existing = cursor.fetchone()
            if (
                existing is not None
                and _row_value(existing, "fingerprint", 0) != trace_fingerprint
            ):
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "trace idempotency conflict",
                    tenant_id=event.tenant_id,
                )

    def list_for_tenant(self, principal: Principal | str) -> list[TraceEvent]:
        tenant_id = _tenant_id_from_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("trace_events")}
                WHERE tenant_id = %s
                ORDER BY timestamp, trace_id
                """,
                (tenant_id,),
            )
            return [
                TraceEvent.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]

    def list_for_run(self, principal: Principal | str, run_id: str) -> list[TraceEvent]:
        tenant_id = _tenant_id_from_principal(principal)
        with self._db.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self._table("trace_events")}
                WHERE tenant_id = %s AND run_id = %s
                ORDER BY timestamp, trace_id
                """,
                (tenant_id, run_id),
            )
            return [
                TraceEvent.model_validate(_payload_from_row(row))
                for row in cursor.fetchall()
            ]


def _tenant_id_from_principal(principal: Principal | str) -> str:
    if isinstance(principal, str):
        return principal
    require_trusted_principal(principal)
    return principal.tenant_id


class PostgresPersistence:
    """Convenience bundle for sharing one Postgres connection configuration."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_factory: ConnectionFactory | None = None,
        connection: Any | None = None,
        schema: str = "public",
    ) -> None:
        self._db = _PostgresDatabase(
            dsn=dsn,
            connection_factory=connection_factory,
            connection=connection,
            schema=schema,
        )
        self.approvals = PostgresApprovalStore(_database=self._db)
        self.calendar = PostgresCalendarStore(_database=self._db)
        self.event_store = PostgresEventStore(_database=self._db)
        self.memory = PostgresMemoryStore(_database=self._db)
        self.outbox = PostgresOutbox(_database=self._db)
        self.scheduler = PostgresReminderScheduler(_database=self._db)
        self.states = PostgresWorkflowStateStore(_database=self._db)
        self.traces = PostgresTraceRecorder(_database=self._db)

    def ensure_schema(self) -> None:
        self._db.ensure_schema()


def build_postgres_persistence(
    *,
    database_url: str | None = None,
    dsn: str | None = None,
    schema: str = "public",
    ensure: bool = True,
) -> PostgresPersistence:
    """Build a Postgres persistence bundle for infrastructure bootstrap."""

    persistence = PostgresPersistence(dsn=database_url or dsn, schema=schema)
    if ensure:
        persistence.ensure_schema()
    return persistence


def _schema_statements(db: _PostgresDatabase) -> tuple[str, ...]:
    events = db.table("events")
    outbox = db.table("outbox")
    workflow_states = db.table("workflow_states")
    approvals = db.table("approvals")
    calendar_events = db.table("calendar_events")
    scheduled_reminders = db.table("scheduled_reminders")
    memory_records = db.table("memory_records")
    trace_events = db.table("trace_events")
    return (
        f"CREATE SCHEMA IF NOT EXISTS {db.schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {events} (
            tenant_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            fingerprint TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, event_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS assistant_events_tenant_time_idx ON {events} (tenant_id, occurred_at)",
        f"""
        CREATE TABLE IF NOT EXISTS {outbox} (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            message_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            dispatch_status TEXT NOT NULL,
            claim_token TEXT,
            claim_owner TEXT,
            claimed_until TIMESTAMPTZ,
            next_attempt_at TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL,
            published_at TIMESTAMPTZ,
            event_payload JSONB NOT NULL,
            fingerprint TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (tenant_id, idempotency_key),
            UNIQUE (tenant_id, message_id)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS assistant_outbox_claim_idx
        ON {outbox} (tenant_id, dispatch_status, claimed_until, next_attempt_at, created_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {workflow_states} (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            step TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            payload_fingerprint TEXT,
            fingerprint TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (tenant_id, idempotency_key),
            UNIQUE (tenant_id, workflow_id)
        )
        """,
        f"""
        ALTER TABLE {workflow_states}
        ADD COLUMN IF NOT EXISTS payload_fingerprint TEXT
        """,
        f"""
        CREATE INDEX IF NOT EXISTS assistant_workflow_states_status_idx
        ON {workflow_states} (tenant_id, status, updated_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {approvals} (
            tenant_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            approval_id TEXT NOT NULL,
            action TEXT NOT NULL,
            resource TEXT NOT NULL,
            tier TEXT NOT NULL,
            workflow_kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ,
            fingerprint TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (tenant_id, approval_id),
            UNIQUE (tenant_id, principal_id, workflow_kind, idempotency_key)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS assistant_approvals_pending_idx
        ON {approvals} (tenant_id, principal_id, status, created_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {calendar_events} (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            event_id TEXT NOT NULL,
            title TEXT NOT NULL,
            starts_at TIMESTAMPTZ NOT NULL,
            request_fingerprint TEXT NOT NULL,
            request_payload JSONB NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, idempotency_key),
            UNIQUE (tenant_id, event_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS assistant_calendar_events_starts_idx ON {calendar_events} (tenant_id, starts_at)",
        f"""
        CREATE TABLE IF NOT EXISTS {scheduled_reminders} (
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            reminder_id TEXT NOT NULL,
            calendar_event_id TEXT NOT NULL,
            notify_at TIMESTAMPTZ NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            sent BOOLEAN NOT NULL DEFAULT false,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, idempotency_key),
            UNIQUE (tenant_id, reminder_id)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS assistant_scheduled_reminders_due_idx
        ON {scheduled_reminders} (tenant_id, sent, notify_at, reminder_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {memory_records} (
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            memory_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT NOT NULL,
            confirmed BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL,
            fingerprint TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (tenant_id, memory_id)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS assistant_memory_records_lookup_idx
        ON {memory_records} (tenant_id, user_id, kind, confirmed, created_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {trace_events} (
            tenant_id TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            parent_event_id TEXT,
            fingerprint TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, trace_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS assistant_trace_events_run_idx ON {trace_events} (tenant_id, run_id, timestamp)",
        f"CREATE INDEX IF NOT EXISTS assistant_trace_events_tenant_idx ON {trace_events} (tenant_id, timestamp)",
    )
