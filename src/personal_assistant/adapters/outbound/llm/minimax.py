"""MiniMax Token Plan LLM adapter."""

from __future__ import annotations

from personal_assistant.adapters.outbound.llm.anthropic import AnthropicCompatibleLLMProvider, UrlOpen


class MiniMaxLLMProvider(AnthropicCompatibleLLMProvider):
    """MiniMax-M3 over MiniMax's Anthropic-compatible Messages API."""

    provider = "minimax"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimax.io/anthropic",
        model: str = "MiniMax-M3",
        timeout_seconds: float = 30.0,
        urlopen: UrlOpen | None = None,
    ) -> None:
        kwargs = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "auth_header": "authorization",
            "timeout_seconds": timeout_seconds,
        }
        if urlopen is not None:
            kwargs["urlopen"] = urlopen
        super().__init__(**kwargs)
