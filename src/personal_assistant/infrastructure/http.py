"""FastAPI composition root for the local assistant runtime.

Facade module: the implementation was split in phase 15 into focused
``http_*`` siblings (models, errors, auth, worker, container, converters,
telegram replies/transcription, routes, and app factory) so each file stays
under the mutation-site budget. This module preserves the public and test import
surface: existing consumers keep importing from
``personal_assistant.infrastructure.http``.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from personal_assistant.adapters.inbound.api import normalize_telegram_webhook
from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
)
from personal_assistant.infrastructure.http_app import create_app
from personal_assistant.infrastructure.http_auth import (
    MAX_TELEGRAM_AUDIO_BYTES,
    SUPPORTED_TRANSCRIPTION_EXTENSIONS,
    TELEGRAM_WEBHOOK_SECRET_HEADER,
    _require_telegram_webhook_secret,
    current_principal,
    telegram_principal,
)
from personal_assistant.infrastructure.http_auth_whatsapp import (
    verify_whatsapp_signature,
    whatsapp_principal,
)
from personal_assistant.infrastructure.http_container import (
    build_runtime_container,
)
from personal_assistant.infrastructure.http_converters import (
    _approval_id,
    _approval_status_from_pending,
    _approval_view_from_pending,
    _effective_idempotency_key,
    _pending_approval_from_request,
    _pending_status_from_approval,
    _reminder_response,
    _workflow_input_from_pending,
)
from personal_assistant.infrastructure.http_errors import _status_for_error
from personal_assistant.infrastructure.http_models import (
    AdminGuardrailMetricsResponse,
    AdminMetricsResponse,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalView,
    DeliveryCountsResponse,
    GuardrailCategoryMetricsResponse,
    HealthResponse,
    OperationalHealthResponse,
    OutboxResolveRequest,
    OutboxResolveResponse,
    ReadinessResponse,
    ReminderCommandRequest,
    ReminderCommandResponse,
    TelegramWebhookResponse,
)
from personal_assistant.infrastructure.http_models_whatsapp import (
    WhatsAppWebhookResponse,
)
from personal_assistant.infrastructure.http_telegram_replies import (
    _send_telegram_audio_reply,
    _send_telegram_reply,
    _should_send_audio_reply,
    _trace_telegram_audio_reply_failure,
)
from personal_assistant.infrastructure.http_telegram_transcription import (
    _transcribe_telegram_media,
    _transcription_filename,
)
from personal_assistant.infrastructure.http_worker import (
    _readiness_snapshot,
    _run_reminder_worker_loop,
    _utcnow,
)
from personal_assistant.infrastructure.migrations import migration_status

__all__ = [
    "MAX_TELEGRAM_AUDIO_BYTES",
    "SUPPORTED_TRANSCRIPTION_EXTENSIONS",
    "TELEGRAM_WEBHOOK_SECRET_HEADER",
    "UTC",
    "AdminGuardrailMetricsResponse",
    "AdminMetricsResponse",
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalView",
    "DeliveryCountsResponse",
    "GuardrailCategoryMetricsResponse",
    "HealthResponse",
    "OperationalHealthResponse",
    "OutboxResolveRequest",
    "OutboxResolveResponse",
    "ReadinessResponse",
    "ReminderCommandRequest",
    "ReminderCommandResponse",
    "TelegramBotApiClient",
    "TelegramWebhookResponse",
    "WhatsAppWebhookResponse",
    "_approval_id",
    "_approval_status_from_pending",
    "_approval_view_from_pending",
    "_effective_idempotency_key",
    "_pending_approval_from_request",
    "_pending_status_from_approval",
    "_readiness_snapshot",
    "_reminder_response",
    "_require_telegram_webhook_secret",
    "_run_reminder_worker_loop",
    "_send_telegram_audio_reply",
    "_send_telegram_reply",
    "_should_send_audio_reply",
    "_status_for_error",
    "_trace_telegram_audio_reply_failure",
    "_transcribe_telegram_media",
    "_transcription_filename",
    "_utcnow",
    "_workflow_input_from_pending",
    "app",
    "build_runtime_container",
    "create_app",
    "current_principal",
    "datetime",
    "migration_status",
    "normalize_telegram_webhook",
    "secrets",
    "telegram_principal",
    "verify_whatsapp_signature",
    "whatsapp_principal",
]

app = create_app()
