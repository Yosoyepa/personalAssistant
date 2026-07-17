"""Anthropic Messages API compatible LLM adapter.

This adapter is intentionally provider-neutral enough for Claude-compatible
gateways such as AeroLink while keeping provider details outside application
use cases and domain code.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError
from urllib import request as urllib_request

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import LLMRequest, LLMResult
from personal_assistant.application.ports.prompts import PromptCatalogPort
from personal_assistant.application.services.prompts import LLM_JSON_SYSTEM_PROMPT_ID
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode


UrlOpen = Callable[..., Any]


def _extract_text(payload: Mapping[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    decoded = json.loads(stripped[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError("LLM response JSON must be an object")
    return decoded


class AnthropicCompatibleLLMProvider:
    """Small stdlib client for Anthropic-compatible `/v1/messages` APIs."""

    provider = "anthropic_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        prompt_catalog: PromptCatalogPort,
        anthropic_version: str = "2023-06-01",
        auth_header: str = "x-api-key",
        timeout_seconds: float = 30.0,
        urlopen: UrlOpen = urllib_request.urlopen,
    ) -> None:
        if not api_key.strip():
            raise ValueError("LLM API key is required")
        if not base_url.strip():
            raise ValueError("LLM base URL is required")
        if not model.strip():
            raise ValueError("LLM model is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._prompt_catalog = prompt_catalog
        self._anthropic_version = anthropic_version
        self._auth_header = auth_header.lower().strip() or "x-api-key"
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        if not budget.can_spend(request.max_tokens):
            raise AssistantError(ErrorCode.TOKEN_BUDGET_EXCEEDED, "LLM token budget exceeded")
        system_prompt = self._prompt_catalog.render(
            LLM_JSON_SYSTEM_PROMPT_ID,
            {"schema_name": request.schema_name},
        )
        body = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": system_prompt.text,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self._anthropic_version,
        }
        if self._auth_header == "authorization":
            headers["Authorization"] = f"Bearer {self._api_key}"
        else:
            headers["x-api-key"] = self._api_key

        req = urllib_request.Request(
            f"{self._base_url}/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:500]
            raise AssistantError(
                ErrorCode.INTERNAL_ERROR,
                f"LLM provider HTTP {exc.code}: {details or exc.reason}",
            ) from exc
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise AssistantError(ErrorCode.INTERNAL_ERROR, "LLM provider returned invalid response")

        text = _extract_text(decoded)
        data = _parse_json_object(text)
        raw_usage = decoded.get("usage")
        usage = raw_usage if isinstance(raw_usage, Mapping) else {}
        return LLMResult(
            provider=self.provider,
            model=str(decoded.get("model") or self._model),
            data=data,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
