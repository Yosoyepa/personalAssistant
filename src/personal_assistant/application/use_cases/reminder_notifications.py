"""Durable outbox dispatcher for reminder notifications facade.

Facade module: implementation was split into focused siblings (helpers, dispatch)
to keep each file under the mutation-site budget while preserving the public API.
"""

from __future__ import annotations

from personal_assistant.application.use_cases.reminder_notifications_dispatch import (
    DispatchDueReminders,
)
from personal_assistant.application.use_cases.reminder_notifications_helpers import (
    MAX_DELIVERY_ATTEMPTS,
    REMINDER_NOTIFICATION_EVENT_TYPE,
    RETRY_DELAYS,
    Clock,
    OutboxApprovalProvider,
    ReminderDispatchOutcome,
    _attempt_idempotency_key,
    _claim_token,
    _invalid_payload_error,
    _known_error,
    _notification_request,
    _retry_at,
    _unknown_error,
)

__all__ = [
    "MAX_DELIVERY_ATTEMPTS",
    "REMINDER_NOTIFICATION_EVENT_TYPE",
    "RETRY_DELAYS",
    "Clock",
    "DispatchDueReminders",
    "OutboxApprovalProvider",
    "ReminderDispatchOutcome",
    "_attempt_idempotency_key",
    "_claim_token",
    "_invalid_payload_error",
    "_known_error",
    "_notification_request",
    "_retry_at",
    "_unknown_error",
]
