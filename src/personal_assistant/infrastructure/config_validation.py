"""Validation helpers for AppSettings post-init checks."""

from __future__ import annotations

from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_assistant.adapters.outbound.egress import (
    DEFAULT_TELEGRAM_API_URL,
    EgressAllowlist,
    derive_egress_entries,
    require_startup_coverage,
)
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.config_constants import (
    _DISABLED_PROVIDERS,
)
from personal_assistant.infrastructure.validation import validate_identifier


def validate_app_settings(settings: Any) -> None:
    """Validate all runtime invariants on the newly initialized AppSettings."""
    object.__setattr__(
        settings,
        "database_schema",
        validate_identifier(settings.database_schema, field="schema"),
    )
    _validate_worker_settings(settings)
    _validate_llm_and_trace_budget(settings)
    _validate_identity_and_timezone(settings)
    _validate_egress(settings)


def _validate_worker_settings(settings: Any) -> None:
    if not isfinite(settings.reminder_worker_interval_seconds):
        raise ValueError("REMINDER_WORKER_INTERVAL_SECONDS must be finite")
    if settings.reminder_worker_interval_seconds <= 0:
        raise ValueError("REMINDER_WORKER_INTERVAL_SECONDS must be greater than zero")
    if not isfinite(settings.reminder_worker_heartbeat_timeout_seconds):
        raise ValueError("REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS must be finite")
    if settings.reminder_worker_heartbeat_timeout_seconds <= 0:
        raise ValueError(
            "REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS must be greater than zero"
        )
    if (
        settings.reminder_worker_enabled
        and settings.reminder_worker_heartbeat_timeout_seconds
        <= settings.reminder_worker_interval_seconds
    ):
        raise ValueError(
            "REMINDER_WORKER_HEARTBEAT_TIMEOUT_SECONDS must exceed "
            "REMINDER_WORKER_INTERVAL_SECONDS when the worker is enabled"
        )


def _validate_llm_and_trace_budget(settings: Any) -> None:
    if (
        not isinstance(settings.llm_context_window_tokens, int)
        or isinstance(settings.llm_context_window_tokens, bool)
        or settings.llm_context_window_tokens <= 0
    ):
        raise ValueError("LLM_CONTEXT_WINDOW_TOKENS must be a positive integer")
    if (
        not isinstance(settings.trace_retention_days, int)
        or isinstance(settings.trace_retention_days, bool)
        or settings.trace_retention_days <= 0
    ):
        raise ValueError("TRACE_RETENTION_DAYS must be a positive integer")


def _validate_identity_and_timezone(settings: Any) -> None:
    try:
        timezone = ZoneInfo(settings.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("ASSISTANT_TIMEZONE must be a valid IANA timezone") from exc
    object.__setattr__(settings, "timezone", timezone.key)
    if (
        not isinstance(settings.local_auth_principal_id, str)
        or not settings.local_auth_principal_id
        or settings.local_auth_principal_id != settings.local_auth_principal_id.strip()
        or len(settings.local_auth_principal_id) > 200
        or not settings.local_auth_principal_id.isprintable()
    ):
        raise ValueError(
            "LOCAL_AUTH_PRINCIPAL_ID must be valid non-blank identity text"
        )
    try:
        local_auth_permission_tier = PermissionTier(settings.local_auth_permission_tier)
    except (TypeError, ValueError) as exc:
        raise ValueError("LOCAL_AUTH_PERMISSION_TIER must be one of P0-P6") from exc
    object.__setattr__(settings, "local_auth_permission_tier", local_auth_permission_tier)


def _validate_egress(settings: Any) -> None:
    derived_egress = derive_egress_entries(
        llm_base_url=settings.llm_base_url,
        transcription_base_url=settings.transcription_base_url,
        tts_base_url=settings.tts_base_url,
        telegram_bot_token_configured=bool(settings.telegram_bot_token),
    )
    explicit_egress = frozenset(
        entry.strip() for entry in settings.egress_allowed_hosts if entry.strip()
    )
    effective_egress = explicit_egress or derived_egress
    egress_allowlist = EgressAllowlist.from_entries(effective_egress)
    object.__setattr__(settings, "egress_allowed_hosts", effective_egress)
    required_egress: dict[str, str] = {}
    if settings.llm_provider not in _DISABLED_PROVIDERS and settings.llm_base_url:
        required_egress["LLM_PROVIDER"] = settings.llm_base_url
    if (
        settings.transcription_provider not in _DISABLED_PROVIDERS
        and settings.transcription_base_url
    ):
        required_egress["TRANSCRIPTION_PROVIDER"] = settings.transcription_base_url
    if settings.tts_provider not in _DISABLED_PROVIDERS and settings.tts_base_url:
        required_egress["TTS_PROVIDER"] = settings.tts_base_url
    if settings.telegram_bot_token:
        required_egress["TELEGRAM_BOT_TOKEN"] = DEFAULT_TELEGRAM_API_URL
    require_startup_coverage(egress_allowlist, required_egress)
