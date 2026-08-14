"""Durable outbox worker and operator CLI.

Facade and CLI composition module: the implementation was split in phase 17 into
focused ``worker_*`` siblings (runtime loop and approval policy, and CLI parsing
and formatting helpers) so each file stays under the mutation-site budget. This
module preserves the public and test import surface: existing consumers keep
importing from ``personal_assistant.infrastructure.worker``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.worker_cli import (
    _parser,
    _print_rows,
    _safe_message,
    _timestamp,
)
from personal_assistant.infrastructure.worker_runtime import (
    Clock,
    ReminderWorker,
    ReminderWorkerTick,
    RuntimeNotificationApprovalPolicy,
    Sleeper,
    StopPredicate,
    utc_now,
)

__all__ = [
    "Clock",
    "ReminderWorker",
    "ReminderWorkerTick",
    "RuntimeNotificationApprovalPolicy",
    "Sleeper",
    "StopPredicate",
    "_parser",
    "_print_rows",
    "_runtime",
    "_safe_message",
    "_timestamp",
    "main",
    "utc_now",
]


def _runtime(*, require_provider: bool) -> tuple[Any, Principal, Any]:
    from personal_assistant.infrastructure.bootstrap import (
        build_container,
        build_egress_allowlist,
        log_egress_audit,
    )
    from personal_assistant.infrastructure.config import AppSettings

    settings = AppSettings.from_env()
    if settings.persistence_backend != "postgres":
        raise RuntimeError("postgres_required")
    if require_provider and not settings.telegram_bot_token:
        raise RuntimeError("telegram_not_configured")
    log_egress_audit(settings)
    notifications = None
    if settings.telegram_bot_token:
        from personal_assistant.adapters.outbound.notifications.telegram import (
            TelegramBotApiClient,
            TelegramNotificationTool,
        )

        notifications = TelegramNotificationTool(
            TelegramBotApiClient(
                token=settings.telegram_bot_token,
                egress_allowlist=build_egress_allowlist(settings),
            )
        )
    container = build_container(
        settings=settings,
        notifications=notifications,
        approve_reminder_notifications=True,
        reminder_minutes_before=settings.reminder_minutes_before,
        llm_context_window_tokens=settings.llm_context_window_tokens,
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
