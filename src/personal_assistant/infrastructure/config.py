"""Runtime configuration for local API, Telegram, and admin surfaces."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_assistant.domain.common.permissions import PermissionTier


DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"
DEFAULT_MINIMAX_TTS_BASE_URL = "https://api.minimax.io"
DEFAULT_MINIMAX_TTS_MODEL = "speech-2.8-turbo"


def _load_env_file() -> dict[str, str]:
    configured = os.getenv("APP_ENV_FILE")
    if configured is not None:
        env_path = configured.strip()
        if env_path.lower() in {"", "disabled", "none"}:
            return {}
    else:
        env_path = ".env"
    path = Path(env_path)
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _env(name: str, file_values: dict[str, str], default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        value = file_values.get(name)
    if value is None:
        return default
    return value


def _optional_env(name: str, file_values: dict[str, str]) -> str | None:
    value = _env(name, file_values)
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_bool(name: str, file_values: dict[str, str], default: bool = False) -> bool:
    value = _env(name, file_values, "true" if default else "false").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _env_permission_tier(
    name: str,
    file_values: dict[str, str],
    default: PermissionTier,
) -> PermissionTier:
    configured = _env(name, file_values, default.value).strip().upper()
    try:
        return PermissionTier(configured)
    except ValueError as exc:
        raise ValueError(f"{name} must be one of P0-P6") from exc


def load_persistence_settings_from_env() -> tuple[str, str | None]:
    file_values = _load_env_file()
    return (
        _env("PERSISTENCE_BACKEND", file_values, "memory").strip().lower() or "memory",
        _optional_env("DATABASE_URL", file_values),
    )


@dataclass(frozen=True, slots=True)
class AppSettings:
    tenant_id: str = "personal"
    timezone: str = "America/Bogota"
    reply_locale: str = "es"
    persistence_backend: str = "memory"
    database_url: str | None = None
    telegram_webhook_secret: str = ""
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: frozenset[str] = frozenset()
    llm_provider: str = "disabled"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_auth_header: str = "x-api-key"
    llm_anthropic_version: str = "2023-06-01"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 512
    transcription_provider: str = "disabled"
    transcription_api_key: str | None = None
    transcription_base_url: str | None = None
    transcription_model: str | None = None
    transcription_timeout_seconds: float = 60.0
    tts_provider: str = "disabled"
    tts_api_key: str | None = None
    tts_base_url: str | None = None
    tts_model: str | None = None
    tts_voice_id: str = "male-qn-qingse"
    tts_audio_format: str = "mp3"
    tts_language_boost: str | None = "Spanish"
    tts_timeout_seconds: float = 30.0
    tts_max_reply_characters: int = 280
    telegram_audio_reply_mode: str = "disabled"
    admin_token: str | None = field(default=None, repr=False)
    local_auth_principal_id: str = "local-user"
    local_auth_permission_tier: PermissionTier = PermissionTier.P5
    public_base_url: str | None = None
    reminder_worker_enabled: bool = False
    reminder_worker_interval_seconds: float = 15.0
    reminder_minutes_before: int = 30

    def __post_init__(self) -> None:
        try:
            timezone = ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(
                "ASSISTANT_TIMEZONE must be a valid IANA timezone"
            ) from exc
        object.__setattr__(self, "timezone", timezone.key)
        if (
            not isinstance(self.local_auth_principal_id, str)
            or not self.local_auth_principal_id
            or self.local_auth_principal_id != self.local_auth_principal_id.strip()
            or len(self.local_auth_principal_id) > 200
            or not self.local_auth_principal_id.isprintable()
        ):
            raise ValueError(
                "LOCAL_AUTH_PRINCIPAL_ID must be valid non-blank identity text"
            )
        try:
            local_auth_permission_tier = PermissionTier(self.local_auth_permission_tier)
        except (TypeError, ValueError) as exc:
            raise ValueError("LOCAL_AUTH_PERMISSION_TIER must be one of P0-P6") from exc
        object.__setattr__(
            self,
            "local_auth_permission_tier",
            local_auth_permission_tier,
        )

    @classmethod
    def from_env(cls) -> "AppSettings":
        file_values = _load_env_file()
        llm_provider = (
            _env("LLM_PROVIDER", file_values, "disabled").strip().lower() or "disabled"
        )
        tts_provider = (
            _env("TTS_PROVIDER", file_values, "disabled").strip().lower() or "disabled"
        )
        interval = _env("REMINDER_WORKER_INTERVAL_SECONDS", file_values, "15")
        reminder_minutes_before = _env("REMINDER_MINUTES_BEFORE", file_values, "30")
        llm_timeout = _env("LLM_TIMEOUT_SECONDS", file_values, "30")
        llm_max_tokens = _env("LLM_MAX_TOKENS", file_values, "512")
        transcription_timeout = _env("TRANSCRIPTION_TIMEOUT_SECONDS", file_values, "60")
        tts_timeout = _env("TTS_TIMEOUT_SECONDS", file_values, "30")
        tts_max_reply_characters = _env("TTS_MAX_REPLY_CHARACTERS", file_values, "280")
        return cls(
            tenant_id=_env("ASSISTANT_TENANT_ID", file_values, "personal").strip()
            or "personal",
            timezone=_env("ASSISTANT_TIMEZONE", file_values, "America/Bogota").strip()
            or "America/Bogota",
            reply_locale=_env("ASSISTANT_REPLY_LOCALE", file_values, "es").strip()
            or "es",
            persistence_backend=_env("PERSISTENCE_BACKEND", file_values, "memory")
            .strip()
            .lower()
            or "memory",
            database_url=_optional_env("DATABASE_URL", file_values),
            telegram_webhook_secret=_env(
                "TELEGRAM_WEBHOOK_SECRET", file_values
            ).strip(),
            telegram_bot_token=_optional_env("TELEGRAM_BOT_TOKEN", file_values),
            telegram_allowed_user_ids=_parse_csv(
                _env("TELEGRAM_ALLOWED_USER_IDS", file_values)
            ),
            llm_provider=llm_provider,
            llm_api_key=(
                _optional_env("LLM_API_KEY", file_values)
                or _optional_env("MINIMAX_API_KEY", file_values)
                or _optional_env("AEROLINK_API_KEY", file_values)
                or _optional_env("ANTHROPIC_API_KEY", file_values)
                or _optional_env("ANTHROPIC_AUTH_TOKEN", file_values)
            ),
            llm_base_url=(
                _optional_env("LLM_BASE_URL", file_values)
                or _optional_env("MINIMAX_BASE_URL", file_values)
                or _optional_env("AEROLINK_BASE_URL", file_values)
                or _optional_env("ANTHROPIC_BASE_URL", file_values)
                or (
                    DEFAULT_MINIMAX_BASE_URL
                    if llm_provider
                    in {"minimax", "minimax_anthropic", "minimax-anthropic"}
                    else None
                )
            ),
            llm_model=(
                _optional_env("LLM_MODEL", file_values)
                or _optional_env("MINIMAX_MODEL", file_values)
                or _optional_env("AEROLINK_MODEL", file_values)
                or _optional_env("ANTHROPIC_MODEL", file_values)
                or (
                    DEFAULT_MINIMAX_MODEL
                    if llm_provider
                    in {"minimax", "minimax_anthropic", "minimax-anthropic"}
                    else None
                )
            ),
            llm_auth_header=_env("LLM_AUTH_HEADER", file_values, "x-api-key").strip()
            or "x-api-key",
            llm_anthropic_version=_env(
                "LLM_ANTHROPIC_VERSION", file_values, "2023-06-01"
            ).strip()
            or "2023-06-01",
            llm_timeout_seconds=max(float(llm_timeout), 1.0),
            llm_max_tokens=max(int(llm_max_tokens), 1),
            transcription_provider=_env(
                "TRANSCRIPTION_PROVIDER", file_values, "disabled"
            )
            .strip()
            .lower()
            or "disabled",
            transcription_api_key=(
                _optional_env("TRANSCRIPTION_API_KEY", file_values)
                or _optional_env("GROQ_API_KEY", file_values)
                or _optional_env("AEROLINK_API_KEY", file_values)
            ),
            transcription_base_url=_optional_env("TRANSCRIPTION_BASE_URL", file_values)
            or _optional_env("AEROLINK_BASE_URL", file_values),
            transcription_model=_optional_env("TRANSCRIPTION_MODEL", file_values),
            transcription_timeout_seconds=max(float(transcription_timeout), 1.0),
            tts_provider=tts_provider,
            tts_api_key=_optional_env("TTS_API_KEY", file_values)
            or _optional_env("MINIMAX_API_KEY", file_values),
            tts_base_url=(
                _optional_env("TTS_BASE_URL", file_values)
                or _optional_env("MINIMAX_TTS_BASE_URL", file_values)
                or (
                    DEFAULT_MINIMAX_TTS_BASE_URL
                    if tts_provider in {"minimax", "minimax_tts", "minimax-tts"}
                    else None
                )
            ),
            tts_model=(
                _optional_env("TTS_MODEL", file_values)
                or _optional_env("MINIMAX_TTS_MODEL", file_values)
                or (
                    DEFAULT_MINIMAX_TTS_MODEL
                    if tts_provider in {"minimax", "minimax_tts", "minimax-tts"}
                    else None
                )
            ),
            tts_voice_id=_env("TTS_VOICE_ID", file_values, "male-qn-qingse").strip()
            or "male-qn-qingse",
            tts_audio_format=_env("TTS_AUDIO_FORMAT", file_values, "mp3")
            .strip()
            .lower()
            or "mp3",
            tts_language_boost=_optional_env("TTS_LANGUAGE_BOOST", file_values)
            or "Spanish",
            tts_timeout_seconds=max(float(tts_timeout), 1.0),
            tts_max_reply_characters=max(int(tts_max_reply_characters), 1),
            telegram_audio_reply_mode=_env(
                "TELEGRAM_AUDIO_REPLY_MODE", file_values, "disabled"
            )
            .strip()
            .lower()
            or "disabled",
            admin_token=_optional_env("ADMIN_TOKEN", file_values),
            local_auth_principal_id=_env(
                "LOCAL_AUTH_PRINCIPAL_ID", file_values, "local-user"
            ).strip(),
            local_auth_permission_tier=_env_permission_tier(
                "LOCAL_AUTH_PERMISSION_TIER",
                file_values,
                PermissionTier.P5,
            ),
            public_base_url=_optional_env("PUBLIC_BASE_URL", file_values),
            reminder_worker_enabled=_env_bool("REMINDER_WORKER_ENABLED", file_values),
            reminder_worker_interval_seconds=max(float(interval), 1.0),
            reminder_minutes_before=max(int(reminder_minutes_before), 1),
        )


def _parse_csv(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())
