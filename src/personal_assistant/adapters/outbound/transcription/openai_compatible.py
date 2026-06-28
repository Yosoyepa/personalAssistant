"""OpenAI-style audio transcription adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError
from urllib import request as urllib_request
from uuid import uuid4

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AudioTranscriptionRequest, AudioTranscriptionResult
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode


UrlOpen = Callable[..., Any]


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file(boundary: str, request: AudioTranscriptionRequest) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{request.filename}"\r\n'
        f"Content-Type: {request.content_type}\r\n\r\n"
    ).encode("utf-8")
    return header + request.data + b"\r\n"


class OpenAICompatibleTranscriptionProvider:
    """Small stdlib client for `/v1/audio/transcriptions` compatible APIs."""

    provider = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        urlopen: UrlOpen = urllib_request.urlopen,
    ) -> None:
        if not api_key.strip():
            raise ValueError("transcription API key is required")
        if not base_url.strip():
            raise ValueError("transcription base URL is required")
        if not model.strip():
            raise ValueError("transcription model is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def transcribe(
        self,
        request: AudioTranscriptionRequest,
        *,
        budget: TokenBudget,
    ) -> AudioTranscriptionResult:
        if not budget.can_spend(1):
            raise AssistantError(ErrorCode.TOKEN_BUDGET_EXCEEDED, "transcription budget exceeded")
        boundary = f"pa-{uuid4().hex}"
        body = bytearray()
        body.extend(_multipart_field(boundary, "model", self._model))
        if request.language:
            body.extend(_multipart_field(boundary, "language", request.language))
        if request.prompt:
            body.extend(_multipart_field(boundary, "prompt", request.prompt))
        body.extend(_multipart_file(boundary, request))
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = urllib_request.Request(
            f"{self._base_url}/v1/audio/transcriptions",
            data=bytes(body),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "personal-assistant/0.1",
            },
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:500]
            raise AssistantError(
                ErrorCode.INTERNAL_ERROR,
                f"transcription provider HTTP {exc.code}: {details or exc.reason}",
            ) from exc
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise AssistantError(ErrorCode.INTERNAL_ERROR, "transcription provider returned invalid response")
        text = decoded.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AssistantError(ErrorCode.INTERNAL_ERROR, "transcription provider returned empty text")
        return AudioTranscriptionResult(
            provider=self.provider,
            model=str(decoded.get("model") or self._model),
            text=text.strip(),
            input_tokens=1,
        )
