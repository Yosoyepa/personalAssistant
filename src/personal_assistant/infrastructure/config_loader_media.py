"""Media provider configuration loader (transcription, TTS)."""

from __future__ import annotations

from typing import Any

from personal_assistant.infrastructure.config_constants import (
    DEFAULT_MINIMAX_TTS_BASE_URL,
    DEFAULT_MINIMAX_TTS_MODEL,
)
from personal_assistant.infrastructure.config_env import (
    _env,
    _optional_env,
)


def _load_media_kwargs(file_values: dict[str, str]) -> dict[str, Any]:
    tts_provider = (
        _env("TTS_PROVIDER", file_values, "disabled").strip().lower() or "disabled"
    )
    transcription_timeout = _env("TRANSCRIPTION_TIMEOUT_SECONDS", file_values, "60")
    tts_timeout = _env("TTS_TIMEOUT_SECONDS", file_values, "30")
    tts_max_reply_characters = _env("TTS_MAX_REPLY_CHARACTERS", file_values, "280")
    return {
        "transcription_provider": _env(
            "TRANSCRIPTION_PROVIDER", file_values, "disabled"
        )
        .strip()
        .lower()
        or "disabled",
        "transcription_api_key": (
            _optional_env("TRANSCRIPTION_API_KEY", file_values)
            or _optional_env("GROQ_API_KEY", file_values)
            or _optional_env("AEROLINK_API_KEY", file_values)
        ),
        "transcription_base_url": _optional_env(
            "TRANSCRIPTION_BASE_URL", file_values
        )
        or _optional_env("AEROLINK_BASE_URL", file_values),
        "transcription_model": _optional_env("TRANSCRIPTION_MODEL", file_values),
        "transcription_timeout_seconds": max(float(transcription_timeout), 1.0),
        "tts_provider": tts_provider,
        "tts_api_key": _optional_env("TTS_API_KEY", file_values)
        or _optional_env("MINIMAX_API_KEY", file_values),
        "tts_base_url": (
            _optional_env("TTS_BASE_URL", file_values)
            or _optional_env("MINIMAX_TTS_BASE_URL", file_values)
            or (
                DEFAULT_MINIMAX_TTS_BASE_URL
                if tts_provider in {"minimax", "minimax_tts", "minimax-tts"}
                else None
            )
        ),
        "tts_model": (
            _optional_env("TTS_MODEL", file_values)
            or _optional_env("MINIMAX_TTS_MODEL", file_values)
            or (
                DEFAULT_MINIMAX_TTS_MODEL
                if tts_provider in {"minimax", "minimax_tts", "minimax-tts"}
                else None
            )
        ),
        "tts_voice_id": _env("TTS_VOICE_ID", file_values, "male-qn-qingse").strip()
        or "male-qn-qingse",
        "tts_audio_format": _env("TTS_AUDIO_FORMAT", file_values, "mp3")
        .strip()
        .lower()
        or "mp3",
        "tts_language_boost": _optional_env("TTS_LANGUAGE_BOOST", file_values)
        or "Spanish",
        "tts_timeout_seconds": max(float(tts_timeout), 1.0),
        "tts_max_reply_characters": max(int(tts_max_reply_characters), 1),
    }
