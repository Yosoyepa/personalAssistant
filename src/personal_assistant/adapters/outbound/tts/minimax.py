"""MiniMax text-to-speech adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib import request as urllib_request

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AudioSynthesisRequest, AudioSynthesisResult
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode


UrlOpen = Callable[..., Any]


_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
}


class MiniMaxTTSProvider:
    """MiniMax T2A v2 HTTP adapter."""

    provider = "minimax"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        voice_id: str = "male-qn-qingse",
        audio_format: str = "mp3",
        timeout_seconds: float = 30.0,
        urlopen: UrlOpen = urllib_request.urlopen,
    ) -> None:
        if not api_key.strip():
            raise ValueError("TTS API key is required")
        if not base_url.strip():
            raise ValueError("TTS base URL is required")
        if not model.strip():
            raise ValueError("TTS model is required")
        if audio_format not in _CONTENT_TYPES:
            raise ValueError("TTS audio format must be mp3, wav, or flac")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._voice_id = voice_id
        self._audio_format = audio_format
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def synthesize(
        self,
        request: AudioSynthesisRequest,
        *,
        budget: TokenBudget,
    ) -> AudioSynthesisResult:
        characters = len(request.text)
        if not budget.can_spend(characters):
            raise AssistantError(ErrorCode.TOKEN_BUDGET_EXCEEDED, "TTS character budget exceeded")
        audio_format = request.audio_format or self._audio_format
        body = {
            "model": self._model,
            "text": request.text,
            "stream": False,
            "voice_setting": {
                "voice_id": request.voice_id or self._voice_id,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": audio_format,
                "channel": 1,
            },
            "language_boost": request.language_boost,
            "output_format": "hex",
            "subtitle_enable": False,
        }
        req = urllib_request.Request(
            f"{self._base_url}/v1/t2a_v2",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self._urlopen(req, timeout=self._timeout_seconds) as response:
            raw = response.read()
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise AssistantError(ErrorCode.INTERNAL_ERROR, "TTS provider returned invalid response")
        raw_base_resp = decoded.get("base_resp")
        base_resp = raw_base_resp if isinstance(raw_base_resp, Mapping) else {}
        if int(base_resp.get("status_code") or 0) != 0:
            message = str(base_resp.get("status_msg") or "TTS provider failed")
            raise AssistantError(ErrorCode.INTERNAL_ERROR, message)
        raw_data = decoded.get("data")
        data = raw_data if isinstance(raw_data, Mapping) else {}
        audio_hex = data.get("audio")
        if not isinstance(audio_hex, str) or not audio_hex:
            raise AssistantError(ErrorCode.INTERNAL_ERROR, "TTS provider returned empty audio")
        try:
            audio = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise AssistantError(ErrorCode.INTERNAL_ERROR, "TTS provider returned invalid audio") from exc
        raw_extra = decoded.get("extra_info")
        extra = raw_extra if isinstance(raw_extra, Mapping) else {}
        result_format = str(extra.get("audio_format") or audio_format)
        return AudioSynthesisResult(
            provider=self.provider,
            model=self._model,
            audio=audio,
            content_type=_CONTENT_TYPES.get(result_format, "application/octet-stream"),
            filename_extension=result_format,
            characters=int(extra.get("usage_characters") or characters),
            trace_id=str(decoded.get("trace_id")) if decoded.get("trace_id") else None,
        )
