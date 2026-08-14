"""LLM provider configuration loader from environment."""

from __future__ import annotations

from typing import Any

from personal_assistant.infrastructure.config_constants import (
    DEFAULT_LLM_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MINIMAX_BASE_URL,
    DEFAULT_MINIMAX_MODEL,
)
from personal_assistant.infrastructure.config_env import (
    _env,
    _optional_env,
)


def _load_llm_kwargs(file_values: dict[str, str]) -> dict[str, Any]:
    llm_provider = (
        _env("LLM_PROVIDER", file_values, "disabled").strip().lower() or "disabled"
    )
    llm_timeout = _env("LLM_TIMEOUT_SECONDS", file_values, "30")
    llm_max_tokens = _env("LLM_MAX_TOKENS", file_values, "512")
    llm_context_window_tokens = _env(
        "LLM_CONTEXT_WINDOW_TOKENS",
        file_values,
        str(DEFAULT_LLM_CONTEXT_WINDOW_TOKENS),
    )
    return {
        "llm_provider": llm_provider,
        "llm_api_key": (
            _optional_env("LLM_API_KEY", file_values)
            or _optional_env("MINIMAX_API_KEY", file_values)
            or _optional_env("AEROLINK_API_KEY", file_values)
            or _optional_env("ANTHROPIC_API_KEY", file_values)
            or _optional_env("ANTHROPIC_AUTH_TOKEN", file_values)
        ),
        "llm_base_url": (
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
        "llm_model": (
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
        "llm_auth_header": _env(
            "LLM_AUTH_HEADER", file_values, "x-api-key"
        ).strip()
        or "x-api-key",
        "llm_anthropic_version": _env(
            "LLM_ANTHROPIC_VERSION", file_values, "2023-06-01"
        ).strip()
        or "2023-06-01",
        "llm_timeout_seconds": max(float(llm_timeout), 1.0),
        "llm_max_tokens": max(int(llm_max_tokens), 1),
        "llm_context_window_tokens": int(llm_context_window_tokens),
    }
