"""Durable outbox dispatcher for reminder notifications."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
    DeliveryStatus,
    canonical_utc,
)
from personal_assistant.application.dto.events import OutboxMessage
from personal_assistant.application.ports.notifications import (
    NotificationPort,
    NotificationRequest,
    NotificationResult,
)
from personal_assistant.application.ports.reminder_unit_of_work import (
    ReminderTransaction,
    ReminderUnitOfWork,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import (
    ApprovalGrant,
    PermissionRequest,
    PermissionTier,
    require_approval,
    require_permission,
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


@dataclass(slots=True)
class DispatchDueReminders:
    """Coordinate outbox and scheduler mirror transitions in one unit of work."""

    unit_of_work: ReminderUnitOfWork
    notifications: NotificationPort
    owner: str = field(default_factory=lambda: f"reminder-worker:{uuid4().hex}")
    # Claim one deliberately: no queued message waits behind provider I/O while
    # its lease ages. Increasing this requires per-message lease management.
    claim_limit: int = 1
    lease_seconds: int = 60
    clock: Clock = lambda: datetime.now(UTC)

    def dispatch(
        self,
        principal: Principal,
        now: datetime,
        *,
        approval_provider: OutboxApprovalProvider,
    ) -> ReminderDispatchOutcome:
        now = canonical_utc(now, field="now")
        swept = self._sweep_expired_sending(principal, now)
        claimed, prefailed = self._claim(principal, now)
        published: list[str] = []
        skipped: list[str] = [message.id for message in prefailed]
        uncertain: list[str] = []

        for message in claimed:
            dispatch_key = _attempt_idempotency_key(message)
            approval = approval_provider(principal, message, dispatch_key)
            try:
                require_approval(
                    principal=principal,
                    tier=PermissionTier.P5,
                    approval=approval,
                    action="notification.send",
                    resource=dispatch_key,
                )
            except AssistantError:
                self._release_before_io(principal, message, now)
                skipped.append(message.id)
                continue

            try:
                request = _notification_request(message, dispatch_key)
            except (ValueError, TypeError):
                self._record_malformed(principal, message, self._now())
                skipped.append(message.id)
                continue

            sending = self._confirm_sending(principal, message, self._now())
            try:
                result = self.notifications.send(
                    principal,
                    request,
                    approval=approval,
                )
            except Exception:
                self._record_unknown(principal, sending, self._now())
                uncertain.append(message.id)
                continue

            if (
                result.idempotency_key != request.idempotency_key
                or result.channel != request.channel
            ):
                self._record_unknown(principal, sending, self._now())
                uncertain.append(message.id)
                continue

            final = self._record_result(principal, sending, result, self._now())
            if final.dispatch_status == DeliveryStatus.published:
                if result.notification_id is not None:
                    published.append(result.notification_id)
            elif final.dispatch_status == DeliveryStatus.uncertain:
                uncertain.append(message.id)
            else:
                skipped.append(message.id)

        return ReminderDispatchOutcome(
            claimed_message_ids=tuple(message.id for message in (*claimed, *prefailed)),
            published_notification_ids=tuple(published),
            skipped_message_ids=tuple(skipped),
            uncertain_message_ids=tuple(uncertain),
            swept_message_ids=tuple(swept),
        )

    def list_uncertain(self, principal: Principal) -> list[OutboxMessage]:
        require_permission(
            principal,
            PermissionRequest(
                action="notification.list_uncertain",
                resource="outbox:uncertain",
                required_tier=PermissionTier.P5,
            ),
        )
        with self.unit_of_work.begin(principal) as transaction:
            messages = [
                message
                for message in transaction.outbox.list_for_tenant(principal)
                if message.dispatch_status == DeliveryStatus.uncertain
                and message.event.type == REMINDER_NOTIFICATION_EVENT_TYPE
            ]
        return messages

    def resolve_uncertain(
        self,
        principal: Principal,
        message_id: str,
        *,
        resolution: str,
        now: datetime,
        approval: ApprovalGrant | None = None,
    ) -> OutboxMessage:
        now = canonical_utc(now, field="now")
        require_approval(
            principal=principal,
            tier=PermissionTier.P5,
            approval=approval,
            action="notification.resolve_uncertain",
            resource=f"{message_id}:{resolution}",
        )
        with self.unit_of_work.begin(principal) as transaction:
            current = next(
                (
                    item
                    for item in transaction.outbox.list_for_tenant(principal)
                    if item.id == message_id
                ),
                None,
            )
            if (
                current is not None
                and current.event.type != REMINDER_NOTIFICATION_EVENT_TYPE
            ):
                raise ValueError("message is not a reminder notification")
            if (
                current is not None
                and resolution == "retry"
                and current.attempts >= MAX_DELIVERY_ATTEMPTS
            ):
                raise ValueError("maximum delivery attempts reached")
            if resolution == "delivered":
                message = transaction.outbox.resolve_uncertain_delivered(
                    principal, message_id, published_at=now
                )
            elif resolution == "retry":
                message = transaction.outbox.resolve_uncertain_retry(
                    principal, message_id, next_attempt_at=now
                )
            else:
                raise ValueError("resolution must be delivered or retry")
            self._mirror_terminal(transaction, principal, message)
            transaction.commit()
        return message

    def _claim(
        self, principal: Principal, now: datetime
    ) -> tuple[list[OutboxMessage], list[OutboxMessage]]:
        with self.unit_of_work.begin(principal) as transaction:
            messages = transaction.outbox.claim_due(
                principal,
                now,
                limit=self.claim_limit,
                owner=self.owner,
                lease_seconds=self.lease_seconds,
                event_type=REMINDER_NOTIFICATION_EVENT_TYPE,
            )
            dispatchable: list[OutboxMessage] = []
            prefailed: list[OutboxMessage] = []
            for message in messages:
                try:
                    self._mirror(transaction, principal, message)
                except ValueError:
                    failed = transaction.outbox.mark_claim_failed(
                        principal,
                        message.id,
                        claim_token=_claim_token(message),
                        error=_invalid_payload_error(now),
                    )
                    self._mirror_pre_io_failed(transaction, principal, failed)
                    prefailed.append(failed)
                    continue
                except AssistantError as error:
                    if error.code != ErrorCode.NOT_FOUND:
                        raise
                    failed = transaction.outbox.mark_claim_failed(
                        principal,
                        message.id,
                        claim_token=_claim_token(message),
                        error=_invalid_payload_error(now),
                    )
                    self._mirror_pre_io_failed(transaction, principal, failed)
                    prefailed.append(failed)
                    continue
                dispatchable.append(message)
            transaction.commit()
        return dispatchable, prefailed

    def _sweep_expired_sending(self, principal: Principal, now: datetime) -> list[str]:
        swept: list[str] = []
        with self.unit_of_work.begin(principal) as transaction:
            messages = transaction.outbox.sweep_expired_sending(
                principal,
                now,
                error=_unknown_error(now),
                limit=self.claim_limit,
                event_type=REMINDER_NOTIFICATION_EVENT_TYPE,
            )
            for uncertain in messages:
                self._mirror_terminal(transaction, principal, uncertain)
                swept.append(uncertain.id)
            if swept:
                transaction.commit()
        return swept

    def _confirm_sending(
        self, principal: Principal, message: OutboxMessage, now: datetime
    ) -> OutboxMessage:
        token = _claim_token(message)
        with self.unit_of_work.begin(principal) as transaction:
            sending = transaction.outbox.mark_sending(
                principal, message.id, claim_token=token, started_at=now
            )
            self._mirror(transaction, principal, sending)
            transaction.commit()
        return sending

    def _release_before_io(
        self, principal: Principal, message: OutboxMessage, now: datetime
    ) -> None:
        token = _claim_token(message)
        with self.unit_of_work.begin(principal) as transaction:
            pending = transaction.outbox.release(
                principal,
                message.id,
                claim_token=token,
                next_attempt_at=now + timedelta(seconds=30),
            )
            self._mirror(transaction, principal, pending)
            transaction.commit()

    def _record_unknown(
        self, principal: Principal, sending: OutboxMessage, now: datetime
    ) -> OutboxMessage:
        token = _claim_token(sending)
        with self.unit_of_work.begin(principal) as transaction:
            uncertain = transaction.outbox.mark_uncertain(
                principal,
                sending.id,
                claim_token=token,
                error=_unknown_error(now),
            )
            self._mirror_terminal(transaction, principal, uncertain)
            transaction.commit()
        return uncertain

    def _record_malformed(
        self, principal: Principal, claimed: OutboxMessage, now: datetime
    ) -> OutboxMessage:
        token = _claim_token(claimed)
        error = _invalid_payload_error(now)
        with self.unit_of_work.begin(principal) as transaction:
            failed = transaction.outbox.mark_claim_failed(
                principal, claimed.id, claim_token=token, error=error
            )
            self._mirror_pre_io_failed(transaction, principal, failed)
            transaction.commit()
        return failed

    def _record_result(
        self,
        principal: Principal,
        sending: OutboxMessage,
        result: NotificationResult,
        now: datetime,
    ) -> OutboxMessage:
        token = _claim_token(sending)
        with self.unit_of_work.begin(principal) as transaction:
            if result.outcome == "success":
                final = transaction.outbox.mark_published(
                    principal,
                    sending.id,
                    claim_token=token,
                    published_at=now,
                )
            elif result.outcome == "known-transient":
                error = _known_error(result, now)
                if sending.attempts >= MAX_DELIVERY_ATTEMPTS:
                    final = transaction.outbox.mark_failed(
                        principal, sending.id, claim_token=token, error=error
                    )
                else:
                    final = transaction.outbox.reschedule(
                        principal,
                        sending.id,
                        claim_token=token,
                        next_attempt_at=_retry_at(
                            now,
                            default_delay=RETRY_DELAYS[sending.attempts - 1],
                            retry_after=result.retry_after,
                        ),
                        error=error,
                    )
            elif result.outcome == "permanent":
                final = transaction.outbox.mark_failed(
                    principal,
                    sending.id,
                    claim_token=token,
                    error=_known_error(result, now),
                )
            else:
                final = transaction.outbox.mark_uncertain(
                    principal,
                    sending.id,
                    claim_token=token,
                    error=_unknown_error(now, provider_code=result.provider_code),
                )
            self._mirror_terminal(transaction, principal, final)
            transaction.commit()
        return final

    @staticmethod
    def _mirror(
        transaction: ReminderTransaction,
        principal: Principal,
        message: OutboxMessage,
    ) -> None:
        subject = message.event.subject
        if not subject:
            raise ValueError("notification outbox message requires reminder subject")
        transaction.scheduler.mirror_delivery(principal, subject, message)

    @staticmethod
    def _mirror_pre_io_failed(
        transaction: ReminderTransaction,
        principal: Principal,
        message: OutboxMessage,
    ) -> None:
        subject = message.event.subject
        if not subject:
            return
        try:
            transaction.scheduler.mirror_delivery(principal, subject, message)
        except AssistantError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise

    @staticmethod
    def _mirror_terminal(
        transaction: ReminderTransaction,
        principal: Principal,
        message: OutboxMessage,
    ) -> None:
        subject = message.event.subject
        if not subject:
            return
        try:
            transaction.scheduler.mirror_delivery(principal, subject, message)
        except AssistantError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise

    def _now(self) -> datetime:
        return canonical_utc(self.clock(), field="clock")


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
    if channel != "telegram":
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
