"""Default configuration constants for runtime and provider integrations."""

from __future__ import annotations

DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"
DEFAULT_MINIMAX_TTS_BASE_URL = "https://api.minimax.io"
DEFAULT_MINIMAX_TTS_MODEL = "speech-2.8-turbo"
DEFAULT_DATABASE_SCHEMA = "public"
# Matches the 200k-token context window of the default MiniMax-M3 model and of
# Anthropic Claude models served through the Anthropic-compatible provider.
DEFAULT_LLM_CONTEXT_WINDOW_TOKENS = 200_000
# Default production trace retention window; the audit policy allows 30-90
# days. Pruning itself is operator-invoked, never automatic at runtime.
DEFAULT_TRACE_RETENTION_DAYS = 30

# Provider selector values that leave the provider disabled; mirrors the
# composition-root convention so startup egress validation stays in sync.
_DISABLED_PROVIDERS = frozenset({"", "disabled", "none"})
