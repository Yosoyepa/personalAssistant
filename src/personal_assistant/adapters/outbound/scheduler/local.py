"""Local scheduler for reminder notices."""

from __future__ import annotations

from datetime import datetime, timedelta

from personal_assistant.application.ports.scheduler import ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal, require_trusted_principal


class ReminderScheduler:
    """Stores reminder jobs for local development and tests."""

    def __init__(self) -> None:
        self._jobs_by_key: dict[tuple[str, str], ScheduledReminder] = {}

    def schedule_before_event(
        self,
        principal: Principal,
        *,
        calendar_event_id: str,
        starts_at: datetime,
        channel: str,
        recipient: str,
        body: str,
        minutes_before: int = 30,
        idempotency_key: str,
    ) -> ScheduledReminder:
        require_trusted_principal(principal)
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        key = (principal.tenant_id, idempotency_key)
        existing = self._jobs_by_key.get(key)
        if existing is not None:
            return existing

        job = ScheduledReminder(
            tenant_id=principal.tenant_id,
            calendar_event_id=calendar_event_id,
            notify_at=starts_at - timedelta(minutes=minutes_before),
            channel=channel,
            recipient=recipient,
            body=body,
            idempotency_key=idempotency_key,
        )
        self._jobs_by_key[key] = job
        return job

    def due(self, principal: Principal, now: datetime) -> list[ScheduledReminder]:
        require_trusted_principal(principal)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        due_jobs = [
            job
            for job in self._jobs_by_key.values()
            if job.tenant_id == principal.tenant_id and not job.sent and job.notify_at <= now
        ]
        return sorted(due_jobs, key=lambda job: (job.notify_at, job.reminder_id))

    def mark_sent(self, principal: Principal, reminder_id: str) -> ScheduledReminder:
        require_trusted_principal(principal)
        for key, job in self._jobs_by_key.items():
            if key[0] == principal.tenant_id and job.reminder_id == reminder_id:
                updated = job.model_copy(update={"sent": True})
                self._jobs_by_key[key] = updated
                return updated
        raise AssistantError(ErrorCode.NOT_FOUND, "scheduled reminder not found", tenant_id=principal.tenant_id)
