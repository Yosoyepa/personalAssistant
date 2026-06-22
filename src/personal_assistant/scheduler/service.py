"""Local scheduler for reminder notices."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, Field

from personal_assistant.notifications.local import LocalNotificationTool, NotificationRequest
from personal_assistant.domain.common.permissions import ApprovalGrant
from personal_assistant.domain.common.identity import Principal


class ScheduledReminder(BaseModel):
    reminder_id: str = Field(default_factory=lambda: f"rem_{uuid4().hex}")
    tenant_id: str
    calendar_event_id: str
    notify_at: datetime
    channel: str
    recipient: str
    body: str
    idempotency_key: str
    sent: bool = False


class ReminderScheduler:
    """Stores reminder jobs and can dispatch due notifications."""

    def __init__(self, notification_tool: LocalNotificationTool) -> None:
        self._notification_tool = notification_tool
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
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return [
            job
            for job in self._jobs_by_key.values()
            if job.tenant_id == principal.tenant_id and not job.sent and job.notify_at <= now
        ]

    def dispatch_due(self, principal: Principal, now: datetime, approval: ApprovalGrant) -> list[str]:
        sent_ids: list[str] = []
        for job in self.due(principal, now):
            result = self._notification_tool.send(
                principal,
                NotificationRequest(
                    channel=job.channel,
                    recipient=job.recipient,
                    body=job.body,
                    idempotency_key=job.idempotency_key,
                ),
                approval=approval,
            )
            job.sent = True
            sent_ids.append(result.notification_id)
        return sent_ids
