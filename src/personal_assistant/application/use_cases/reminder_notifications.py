"""Use case for dispatching due reminder notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from personal_assistant.application.ports.notifications import NotificationPort, NotificationRequest
from personal_assistant.application.ports.scheduler import ReminderSchedulerWorkerPort
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


@dataclass(slots=True)
class DispatchDueReminders:
    scheduler: ReminderSchedulerWorkerPort
    notifications: NotificationPort

    def run(self, principal: Principal, now: datetime, approval: ApprovalGrant) -> list[str]:
        sent_ids: list[str] = []
        for job in self.scheduler.due(principal, now):
            result = self.notifications.send(
                principal,
                NotificationRequest(
                    channel=job.channel,
                    recipient=job.recipient,
                    body=job.body,
                    idempotency_key=job.idempotency_key,
                ),
                approval=approval,
            )
            self.scheduler.mark_sent(principal, job.reminder_id)
            sent_ids.append(result.notification_id)
        return sent_ids
