"""Use case for dispatching due reminder notifications."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from personal_assistant.application.ports.notifications import NotificationPort, NotificationRequest
from personal_assistant.application.ports.scheduler import ReminderSchedulerWorkerPort, ScheduledReminder
from personal_assistant.domain.common.exceptions import AssistantError
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant

ReminderNotificationApprovalProvider = Callable[[Principal, ScheduledReminder], ApprovalGrant | None]


@dataclass(frozen=True, slots=True)
class ReminderDispatchOutcome:
    due_reminder_ids: tuple[str, ...]
    sent_notification_ids: tuple[str, ...]
    skipped_reminder_ids: tuple[str, ...]

    @property
    def due_count(self) -> int:
        return len(self.due_reminder_ids)

    @property
    def sent_count(self) -> int:
        return len(self.sent_notification_ids)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_reminder_ids)


@dataclass(slots=True)
class DispatchDueReminders:
    scheduler: ReminderSchedulerWorkerPort
    notifications: NotificationPort

    def dispatch(
        self,
        principal: Principal,
        now: datetime,
        *,
        approval: ApprovalGrant | None = None,
        approval_provider: ReminderNotificationApprovalProvider | None = None,
    ) -> ReminderDispatchOutcome:
        if approval is not None and approval_provider is not None:
            raise ValueError("provide either approval or approval_provider, not both")

        due_jobs = self.scheduler.due(principal, now)
        if approval is not None and len(due_jobs) > 1:
            raise ValueError("a single approval grant cannot dispatch multiple reminder notifications")

        sent_ids: list[str] = []
        skipped_ids: list[str] = []
        for job in due_jobs:
            job_approval = approval_provider(principal, job) if approval_provider is not None else approval
            if job_approval is None:
                skipped_ids.append(job.reminder_id)
                continue

            try:
                result = self.notifications.send(
                    principal,
                    NotificationRequest(
                        channel=job.channel,
                        recipient=job.recipient,
                        body=job.body,
                        idempotency_key=job.idempotency_key,
                    ),
                    approval=job_approval,
                )
            except AssistantError:
                raise
            except Exception:
                skipped_ids.append(job.reminder_id)
                continue
            self.scheduler.mark_sent(principal, job.reminder_id)
            sent_ids.append(result.notification_id)
        return ReminderDispatchOutcome(
            due_reminder_ids=tuple(job.reminder_id for job in due_jobs),
            sent_notification_ids=tuple(sent_ids),
            skipped_reminder_ids=tuple(skipped_ids),
        )

    def run(self, principal: Principal, now: datetime, approval: ApprovalGrant) -> list[str]:
        outcome = self.dispatch(principal, now, approval=approval)
        return list(outcome.sent_notification_ids)
