"""MiniMax Token Plan LLM adapter."""

from __future__ import annotations

from personal_assistant.adapters.outbound.llm.anthropic import AnthropicCompatibleLLMProvider, UrlOpen
from personal_assistant.application.ports.prompts import PromptCatalogPort


class MiniMaxLLMProvider(AnthropicCompatibleLLMProvider):
    """MiniMax-M3 over MiniMax's Anthropic-compatible Messages API."""

    provider = "minimax"

    def __init__(
        self,
        *,
        api_key: str,
        prompt_catalog: PromptCatalogPort,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        urlopen: UrlOpen | None = None,
    ) -> None:
        kwargs = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "prompt_catalog": prompt_catalog,
            "auth_header": "authorization",
            "timeout_seconds": timeout_seconds,
        }
        if urlopen is not None:
            kwargs["urlopen"] = urlopen
        super().__init__(**kwargs)
