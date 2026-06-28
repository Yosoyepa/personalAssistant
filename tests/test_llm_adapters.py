from __future__ import annotations

import json
import unittest

from personal_assistant.adapters.outbound.llm.anthropic import AnthropicCompatibleLLMProvider
from personal_assistant.adapters.outbound.transcription.openai_compatible import OpenAICompatibleTranscriptionProvider
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import AudioTranscriptionRequest, LLMRequest


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class LLMAdapterTests(unittest.TestCase):
    def test_anthropic_compatible_provider_parses_json_content(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(
                {
                    "model": "claude-test",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"is_reminder": true, "title": "comer", "starts_at": "2026-06-20T15:33:00+00:00", "confidence": 0.9}',
                        }
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                }
            )

        provider = AnthropicCompatibleLLMProvider(
            api_key="key",
            base_url="https://aerolink.example",
            model="claude-test",
            urlopen=fake_urlopen,
        )

        result = provider.complete(
            LLMRequest(prompt="extrae", schema_name="reminder_extraction"),
            budget=TokenBudget(limit=1000),
        )

        self.assertEqual(captured["url"], "https://aerolink.example/v1/messages")
        self.assertEqual(result.data["title"], "comer")
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 8)

    def test_openai_compatible_transcription_provider_parses_text(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["content_type"] = req.get_header("Content-type")
            captured["body"] = req.data
            return FakeResponse({"text": "agendarme una cita a las 3:33 para comer"})

        provider = OpenAICompatibleTranscriptionProvider(
            api_key="key",
            base_url="https://stt.example",
            model="whisper-test",
            urlopen=fake_urlopen,
        )

        result = provider.transcribe(
            AudioTranscriptionRequest(
                filename="voice.ogg",
                content_type="audio/ogg",
                data=b"audio-bytes",
            ),
            budget=TokenBudget(limit=1000),
        )

        self.assertEqual(captured["url"], "https://stt.example/v1/audio/transcriptions")
        self.assertIn("multipart/form-data", str(captured["content_type"]))
        self.assertIn(b"audio-bytes", captured["body"])
        self.assertIn("cita", result.text)


if __name__ == "__main__":
    unittest.main()
