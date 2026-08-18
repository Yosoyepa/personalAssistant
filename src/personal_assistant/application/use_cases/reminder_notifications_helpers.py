"""Helper functions, constants, and outcome types for reminder notification dispatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
)
from personal_assistant.application.dto.events import OutboxMessage
from personal_assistant.application.ports.notifications import (
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
)

MAX_DELIVERY_ATTEMPTS = 4
REMINDER_NOTIFICATION_EVENT_TYPE = "notification.requested"
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=5))
Clock = Callable[[], datetime]

OutboxApprovalProvider = Callable[[Principal, OutboxMessage, str], ApprovalGrant | None]


@dataclass(frozen=True, slots=True)
class ReminderDispatchOutcome:
    claimed_message_ids: tuple[str, ...]
    published_notification_ids: tuple[str, ...]
    skipped_message_ids: tuple[str, ...]
    uncertain_message_ids: tuple[str, ...]
    swept_message_ids: tuple[str, ...]

    @property
    def due_reminder_ids(self) -> tuple[str, ...]:
        return self.claimed_message_ids

    @property
    def sent_notification_ids(self) -> tuple[str, ...]:
        return self.published_notification_ids

    @property
    def skipped_reminder_ids(self) -> tuple[str, ...]:
        return self.skipped_message_ids

    @property
    def due_count(self) -> int:
        return len(self.claimed_message_ids)

    @property
    def sent_count(self) -> int:
        return len(self.published_notification_ids)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_message_ids)


def _claim_token(message: OutboxMessage) -> str:
    if not message.claim_token:
        raise RuntimeError("outbox message has no active claim")
    return message.claim_token


def _attempt_idempotency_key(message: OutboxMessage) -> str:
    return f"{message.id}:attempt:{message.attempts + 1}"


def _notification_request(
    message: OutboxMessage, dispatch_key: str
) -> NotificationRequest:
    if message.event.type != REMINDER_NOTIFICATION_EVENT_TYPE:
        raise ValueError("outbox event is not a reminder notification")
    if not message.event.subject:
        raise ValueError("reminder notification requires a scheduler subject")
    data = message.event.data
    channel = data.get("channel")
    recipient = data.get("recipient")
    body = data.get("body")
    if channel not in {"telegram", "whatsapp"}:
        raise ValueError("unsupported notification channel")
    if not isinstance(recipient, str) or not recipient.strip():
        raise ValueError("notification recipient must be non-empty text")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("notification body must be non-empty text")
    return NotificationRequest(
        channel=channel,
        recipient=recipient,
        body=body,
        idempotency_key=dispatch_key,
    )


def _known_error(result: NotificationResult, now: datetime) -> DeliveryError:
    code = result.provider_code
    if code == 429:
        category = DeliveryErrorCategory.rate_limited
        error_code = DeliveryErrorCode.rate_limited
    elif result.outcome == "known-transient" and (code is None or code >= 500):
        category = DeliveryErrorCategory.network
        error_code = DeliveryErrorCode.provider_unavailable
    elif code in {401, 403}:
        category = DeliveryErrorCategory.rejected
        error_code = DeliveryErrorCode.authentication_failed
    else:
        category = DeliveryErrorCategory.rejected
        error_code = DeliveryErrorCode.request_rejected
    return DeliveryError(
        category=category,
        code=error_code,
        provider_code=code,
        occurred_at=now,
    )


def _unknown_error(now: datetime, *, provider_code: int | None = None) -> DeliveryError:
    return DeliveryError(
        category=DeliveryErrorCategory.unknown,
        code=DeliveryErrorCode.unknown,
        provider_code=provider_code,
        occurred_at=now,
    )


def _invalid_payload_error(now: datetime) -> DeliveryError:
    return DeliveryError(
        category=DeliveryErrorCategory.internal,
        code=DeliveryErrorCode.internal_error,
        occurred_at=now,
    )


def _retry_at(
    now: datetime,
    *,
    default_delay: timedelta,
    retry_after: int | None,
) -> datetime:
    seconds = int(default_delay.total_seconds())
    if retry_after is not None:
        seconds = max(seconds, retry_after)
    try:
        return now + timedelta(seconds=seconds)
    except OverflowError:
        return datetime.max.replace(tzinfo=now.tzinfo)
