"""Local scheduler for reminder notices."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from personal_assistant.adapters._in_memory_transaction import (
    ReentrantLock,
    new_reentrant_lock,
)
from personal_assistant.application.dto.delivery import DeliveryStatus
from personal_assistant.application.dto.events import OutboxMessage
from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import (
    Principal,
    require_trusted_principal,
)


@dataclass(frozen=True, slots=True)
class _SchedulerSnapshot:
    jobs_by_key: dict[tuple[str, str], ScheduledReminder]
    key_by_reminder_id: dict[tuple[str, str], str]


class ReminderScheduler:
    """Stores reminder jobs for local development and tests."""

    def __init__(self) -> None:
        self._jobs_by_key: dict[tuple[str, str], ScheduledReminder] = {}
        self._key_by_reminder_id: dict[tuple[str, str], str] = {}
        self._lock = new_reentrant_lock()

    @property
    def _reminder_transaction_lock(self) -> ReentrantLock:
        return self._lock

    def _snapshot_reminder_transaction(self) -> object:
        with self._lock:
            return _SchedulerSnapshot(
                jobs_by_key=deepcopy(self._jobs_by_key),
                key_by_reminder_id=deepcopy(self._key_by_reminder_id),
            )

    def _restore_reminder_transaction(self, snapshot: object) -> None:
        if not isinstance(snapshot, _SchedulerSnapshot):
            raise TypeError("invalid scheduler transaction snapshot")
        with self._lock:
            self._jobs_by_key = deepcopy(snapshot.jobs_by_key)
            self._key_by_reminder_id = deepcopy(snapshot.key_by_reminder_id)

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
        reminder_id: str | None = None,
    ) -> ScheduledReminder:
        require_trusted_principal(principal)
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        starts_at_utc = starts_at.astimezone(UTC)
        notify_at = starts_at_utc - timedelta(minutes=minutes_before)
        key = (principal.tenant_id, idempotency_key)
        job_data: dict[str, object] = {
            "tenant_id": principal.tenant_id,
            "calendar_event_id": calendar_event_id,
            "notify_at": notify_at,
            "timezone": timezone,
            "source_event_id": source_event_id,
            "payload_fingerprint": payload_fingerprint,
            "channel": channel,
            "recipient": recipient,
            "body": body,
            "idempotency_key": idempotency_key,
        }
        if reminder_id is not None:
            job_data["reminder_id"] = reminder_id
        job = ScheduledReminder.model_validate(job_data)
        with self._lock:
            existing = self._jobs_by_key.get(key)
            if existing is not None:
                if (
                    existing.calendar_event_id != job.calendar_event_id
                    or existing.notify_at != job.notify_at
                    or existing.timezone != job.timezone
                    or existing.source_event_id != job.source_event_id
                    or existing.payload_fingerprint != job.payload_fingerprint
                    or existing.channel != job.channel
                    or existing.recipient != job.recipient
                    or existing.body != job.body
                ):
                    raise AssistantError(
                        ErrorCode.CONFLICT,
                        "reminder scheduler idempotency conflict",
                        tenant_id=principal.tenant_id,
                    )
                return existing

            reminder_key = (principal.tenant_id, job.reminder_id)
            existing_key = self._key_by_reminder_id.get(reminder_key)
            if existing_key is not None and existing_key != idempotency_key:
                raise AssistantError(
                    ErrorCode.CONFLICT,
                    "scheduled reminder id conflict",
                    tenant_id=principal.tenant_id,
                )
            self._jobs_by_key[key] = job
            self._key_by_reminder_id[reminder_key] = idempotency_key
            return job

    def due(self, principal: Principal, now: datetime) -> list[ScheduledReminder]:
        require_trusted_principal(principal)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        with self._lock:
            due_jobs = [
                job
                for job in self._jobs_by_key.values()
                if job.tenant_id == principal.tenant_id
                and job.delivery_status == DeliveryStatus.pending
                and job.notify_at <= now
                and (job.next_attempt_at is None or job.next_attempt_at <= now)
            ]
            return sorted(due_jobs, key=lambda job: (job.notify_at, job.reminder_id))

    def mirror_delivery(
        self,
        principal: Principal,
        reminder_id: str,
        message: OutboxMessage,
    ) -> ScheduledReminder:
        require_trusted_principal(principal)
        if (
            message.tenant_id != principal.tenant_id
            or message.event.subject != reminder_id
        ):
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "delivery mirror identity mismatch",
                tenant_id=principal.tenant_id,
            )
        with self._lock:
            for key, job in self._jobs_by_key.items():
                if key[0] != principal.tenant_id or job.reminder_id != reminder_id:
                    continue
                updated = ScheduledReminder.model_validate(
                    job.model_copy(
                        update={
                            "delivery_status": message.dispatch_status,
                            "attempts": message.attempts,
                            "next_attempt_at": message.next_attempt_at,
                            "sending_at": message.sending_at,
                            "published_at": message.published_at,
                            "last_error": message.last_error,
                            "sent": message.dispatch_status != DeliveryStatus.pending,
                        }
                    ).model_dump()
                )
                self._jobs_by_key[key] = updated
                return updated.model_copy(deep=True)
        raise AssistantError(
            ErrorCode.NOT_FOUND,
            "scheduled reminder not found",
            tenant_id=principal.tenant_id,
        )

    def mark_sent(self, principal: Principal, reminder_id: str) -> ScheduledReminder:
        require_trusted_principal(principal)
        with self._lock:
            for key, job in self._jobs_by_key.items():
                if key[0] == principal.tenant_id and job.reminder_id == reminder_id:
                    updated = job.model_copy(
                        update={
                            "delivery_status": DeliveryStatus.published,
                            "sent": True,
                            "published_at": datetime.now(UTC),
                        }
                    )
                    self._jobs_by_key[key] = updated
                    return updated
        raise AssistantError(
            ErrorCode.NOT_FOUND,
            "scheduled reminder not found",
            tenant_id=principal.tenant_id,
        )

    def list_for_tenant(self, principal: Principal) -> list[ScheduledReminder]:
        require_trusted_principal(principal)
        with self._lock:
            return [
                job
                for (tenant_id, _), job in self._jobs_by_key.items()
                if tenant_id == principal.tenant_id
            ]
