"""Durable outbox worker and operator CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import sys
import time
from typing import Any

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m personal_assistant.infrastructure.worker"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run-once")
    commands.add_parser("list-uncertain")
    resolve = commands.add_parser("resolve-uncertain")
    resolve.add_argument("--message-id", required=True)
    resolve.add_argument("--resolution", choices=("delivered", "retry"), required=True)
    resolve.add_argument("--confirm", required=True)
    return parser


def _runtime(*, require_provider: bool) -> tuple[Any, Principal, Any]:
    from personal_assistant.infrastructure.bootstrap import build_container
    from personal_assistant.infrastructure.config import AppSettings

    settings = AppSettings.from_env()
    if settings.persistence_backend != "postgres":
        raise RuntimeError("postgres_required")
    if require_provider and not settings.telegram_bot_token:
        raise RuntimeError("telegram_not_configured")
    notifications = None
    if settings.telegram_bot_token:
        from personal_assistant.adapters.outbound.notifications.telegram import (
            TelegramBotApiClient,
            TelegramNotificationTool,
        )

        notifications = TelegramNotificationTool(
            TelegramBotApiClient(token=settings.telegram_bot_token)
        )
    container = build_container(
        settings=settings,
        notifications=notifications,
        approve_reminder_notifications=True,
        reminder_minutes_before=settings.reminder_minutes_before,
    )
    principal = Principal(
        principal_id="reminder-worker",
        tenant_id=settings.tenant_id,
        auth_subject="reminder-worker",
        auth_provider="worker-runtime",
        permission_tier=PermissionTier.P5,
    )
    principal.mark_trusted("worker-runtime")
    return container, principal, settings


def _safe_message(message: OutboxMessage) -> dict[str, object]:
    error = message.last_error
    return {
        "message_id": message.id,
        "status": message.dispatch_status.value,
        "attempts": message.attempts,
        "claimed_until": _timestamp(message.claimed_until),
        "next_attempt_at": _timestamp(message.next_attempt_at),
        "sending_at": _timestamp(message.sending_at),
        "published_at": _timestamp(message.published_at),
        "error_category": error.category.value if error is not None else None,
        "error_code": error.code.value if error is not None else None,
        "provider_code": error.provider_code if error is not None else None,
    }


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _print_rows(rows: Sequence[dict[str, object]]) -> None:
    print(json.dumps(list(rows), sort_keys=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "resolve-uncertain" and args.confirm != args.message_id:
        _print_rows(({"status": "error", "code": "confirmation_mismatch"},))
        return 2
    try:
        container, principal, _settings = _runtime(
            require_provider=args.command == "run-once"
        )
        dispatcher = container.reminder_notifications
        if args.command == "run-once":
            tick = container.reminder_worker.run_once(principal)
            ids = set(tick.claimed_message_ids) | set(tick.swept_message_ids)
            messages = container.outbox.list_for_tenant(principal)
            _print_rows(
                tuple(_safe_message(item) for item in messages if item.id in ids)
            )
            return 0
        if args.command == "list-uncertain":
            _print_rows(
                tuple(
                    _safe_message(item) for item in dispatcher.list_uncertain(principal)
                )
            )
            return 0
        resolved = dispatcher.resolve_uncertain(
            principal,
            args.message_id,
            resolution=args.resolution,
            now=utc_now(),
            approval=ApprovalGrant.issue(
                principal=principal,
                action="notification.resolve_uncertain",
                resource=f"{args.message_id}:{args.resolution}",
                tier=PermissionTier.P5,
                approval_id=f"worker-cli:{args.message_id}:{args.resolution}",
            ),
        )
        _print_rows((_safe_message(resolved),))
        return 0
    except RuntimeError as exc:
        code = (
            str(exc)
            if str(exc) in {"postgres_required", "telegram_not_configured"}
            else "runtime_error"
        )
        _print_rows(({"status": "error", "code": code},))
        return 1
    except Exception:
        _print_rows(({"status": "error", "code": "operation_failed"},))
        return 1


if __name__ == "__main__":
    sys.exit(main())
