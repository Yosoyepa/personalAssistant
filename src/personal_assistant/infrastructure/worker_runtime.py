"""Durable outbox worker loop and approval policy."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from personal_assistant.application.dto.events import OutboxMessage
from personal_assistant.application.use_cases.reminder_notifications import (
    DispatchDueReminders,
    ReminderDispatchOutcome,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier

Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]
StopPredicate = Callable[[], bool]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RuntimeNotificationApprovalPolicy:
    """Runtime-owned approval policy for one durable delivery attempt."""

    approve_notifications: bool = False
    approval_ttl: timedelta | None = timedelta(minutes=5)
    approval_id_prefix: str = "reminder-worker"

    def approval_for(
        self,
        principal: Principal,
        message: OutboxMessage,
        dispatch_key: str,
        *,
        now: datetime,
    ) -> ApprovalGrant | None:
        if not self.approve_notifications:
            return None
        expires_at = now + self.approval_ttl if self.approval_ttl is not None else None
        return ApprovalGrant.issue(
            principal=principal,
            action="notification.send",
            resource=dispatch_key,
            tier=PermissionTier.P5,
            approval_id=f"{self.approval_id_prefix}:{message.id}:{message.attempts + 1}",
            expires_at=expires_at,
        )


@dataclass(frozen=True, slots=True)
class ReminderWorkerTick:
    ran_at: datetime
    claimed_message_ids: tuple[str, ...]
    due_count: int
    sent_notification_ids: tuple[str, ...]
    skipped_reminder_ids: tuple[str, ...]
    uncertain_message_ids: tuple[str, ...]
    swept_message_ids: tuple[str, ...]

    @classmethod
    def from_outcome(
        cls, *, ran_at: datetime, outcome: ReminderDispatchOutcome
    ) -> ReminderWorkerTick:
        return cls(
            ran_at=ran_at,
            claimed_message_ids=outcome.claimed_message_ids,
            due_count=outcome.due_count,
            sent_notification_ids=outcome.sent_notification_ids,
            skipped_reminder_ids=outcome.skipped_reminder_ids,
            uncertain_message_ids=outcome.uncertain_message_ids,
            swept_message_ids=outcome.swept_message_ids,
        )

    @property
    def sent_count(self) -> int:
        return len(self.sent_notification_ids)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_reminder_ids)


@dataclass(slots=True)
class ReminderWorker:
    dispatcher: DispatchDueReminders
    approval_policy: RuntimeNotificationApprovalPolicy = field(
        default_factory=RuntimeNotificationApprovalPolicy
    )
    clock: Clock = utc_now
    sleep: Sleeper = time.sleep

    def run_once(
        self, principal: Principal, *, now: datetime | None = None
    ) -> ReminderWorkerTick:
        ran_at = now or self.clock()

        def approval_provider(
            inner_principal: Principal,
            message: OutboxMessage,
            dispatch_key: str,
        ) -> ApprovalGrant | None:
            return self.approval_policy.approval_for(
                inner_principal, message, dispatch_key, now=ran_at
            )

        outcome = self.dispatcher.dispatch(
            principal, ran_at, approval_provider=approval_provider
        )
        return ReminderWorkerTick.from_outcome(ran_at=ran_at, outcome=outcome)

    def run_loop(
        self,
        principal: Principal,
        *,
        interval_seconds: float = 30.0,
        max_ticks: int | None = None,
        stop_when: StopPredicate | None = None,
    ) -> list[ReminderWorkerTick]:
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be non-negative")
        if max_ticks is not None and max_ticks < 0:
            raise ValueError("max_ticks must be non-negative")

        ticks: list[ReminderWorkerTick] = []
        while max_ticks is None or len(ticks) < max_ticks:
            if stop_when is not None and stop_when():
                break
            ticks.append(self.run_once(principal))
            if max_ticks is not None and len(ticks) >= max_ticks:
                break
            if stop_when is not None and stop_when():
                break
            self.sleep(interval_seconds)
        return ticks
