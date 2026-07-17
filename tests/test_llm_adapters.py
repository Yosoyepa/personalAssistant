from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError

from personal_assistant.adapters.outbound.llm.anthropic import (
    AnthropicCompatibleLLMProvider,
)
from personal_assistant.adapters.outbound.llm.minimax import MiniMaxLLMProvider
from personal_assistant.adapters.outbound.transcription.openai_compatible import (
    OpenAICompatibleTranscriptionProvider,
)
from personal_assistant.adapters.outbound.tts.minimax import MiniMaxTTSProvider
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import (
    AudioSynthesisRequest,
    AudioTranscriptionRequest,
    LLMRequest,
)
from personal_assistant.application.services.prompts import (
    PromptTemplate,
    StaticPromptCatalog,
)
from personal_assistant.domain.common.exceptions import AssistantError


def _prompt_catalog() -> StaticPromptCatalog:
    return StaticPromptCatalog(
        {
            "llm_json_system": PromptTemplate(
                prompt_id="llm_json_system",
                version="test",
                template="JSON_SYSTEM schema=$schema_name",
                required_variables=("schema_name",),
            )
        }
    )


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
            prompt_catalog=_prompt_catalog(),
            urlopen=fake_urlopen,
        )

        result = provider.complete(
            LLMRequest(prompt="extrae", schema_name="reminder_extraction"),
            budget=TokenBudget(limit=1000),
        )

        self.assertEqual(captured["url"], "https://aerolink.example/v1/messages")
        body = captured["body"]
        self.assertIsInstance(body, dict)
        self.assertEqual(body["system"], "JSON_SYSTEM schema=reminder_extraction")
        self.assertEqual(result.data["title"], "comer")
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 8)

    def test_minimax_provider_uses_token_plan_anthropic_endpoint(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(
                {
                    "model": "MiniMax-M3",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"is_reminder": true, "title": "comer", "starts_at": "2026-06-20T15:33:00+00:00", "confidence": 0.9}',
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 7},
                }
            )

        provider = MiniMaxLLMProvider(
            api_key="sk-cp-test",
            prompt_catalog=_prompt_catalog(),
            base_url="https://api.minimax.io/anthropic",
            model="MiniMax-M3",
            urlopen=fake_urlopen,
        )
        result = provider.complete(
            LLMRequest(prompt="extrae", schema_name="reminder_extraction"),
            budget=TokenBudget(limit=1000),
        )

        self.assertEqual(
            captured["url"], "https://api.minimax.io/anthropic/v1/messages"
        )
        headers = captured["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer sk-cp-test")
        body = captured["body"]
        self.assertIsInstance(body, dict)
        self.assertEqual(body["model"], "MiniMax-M3")
        self.assertEqual(body["system"], "JSON_SYSTEM schema=reminder_extraction")
        self.assertEqual(result.provider, "minimax")
        self.assertEqual(result.data["title"], "comer")

    def test_anthropic_compatible_provider_redacts_http_error_body(self) -> None:
        def fake_urlopen(req, timeout):
            raise HTTPError(
                req.full_url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"invalid api key"}}'),
            )

        provider = AnthropicCompatibleLLMProvider(
            api_key="bad-key",
            base_url="https://aerolink.example",
            model="claude-test",
            prompt_catalog=_prompt_catalog(),
            urlopen=fake_urlopen,
        )

        with self.assertRaises(AssistantError) as ctx:
            provider.complete(
                LLMRequest(prompt="clasifica", schema_name="conversation_intent"),
                budget=TokenBudget(limit=1000),
            )

        self.assertNotIn("invalid api key", str(ctx.exception))
        self.assertEqual(str(ctx.exception), "internal error")
        self.assertEqual(
            len(ctx.exception.response.error.context["message_sha256"]), 64
        )

    def test_openai_compatible_transcription_provider_parses_text(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["content_type"] = req.get_header("Content-type")
            captured["accept"] = req.get_header("Accept")
            captured["user_agent"] = req.get_header("User-agent")
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
        self.assertEqual(captured["accept"], "application/json")
        self.assertEqual(captured["user_agent"], "personal-assistant/0.1")
        self.assertIn(b"audio-bytes", captured["body"])
        self.assertIn("cita", result.text)

    def test_openai_compatible_transcription_provider_redacts_http_error_body(
        self,
    ) -> None:
        def fake_urlopen(req, timeout):
            raise HTTPError(
                req.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"unsupported audio format"}}'),
            )

        provider = OpenAICompatibleTranscriptionProvider(
            api_key="key",
            base_url="https://stt.example",
            model="whisper-test",
            urlopen=fake_urlopen,
        )

        with self.assertRaises(AssistantError) as ctx:
            provider.transcribe(
                AudioTranscriptionRequest(
                    filename="voice.ogg",
                    content_type="audio/ogg",
                    data=b"audio-bytes",
                ),
                budget=TokenBudget(limit=1000),
            )

        self.assertNotIn("unsupported audio format", str(ctx.exception))
        self.assertEqual(str(ctx.exception), "internal error")
        self.assertEqual(
            len(ctx.exception.response.error.context["message_sha256"]), 64
        )

    def test_minimax_tts_provider_decodes_hex_audio(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(
                {
                    "data": {"audio": "6869", "status": 2},
                    "extra_info": {"usage_characters": 4, "audio_format": "mp3"},
                    "trace_id": "trace-1",
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                }
            )

        provider = MiniMaxTTSProvider(
            api_key="sk-cp-test",
            base_url="https://api.minimax.io",
            model="speech-2.8-turbo",
            urlopen=fake_urlopen,
        )
        result = provider.synthesize(
            AudioSynthesisRequest(text="hola", voice_id="male-qn-qingse"),
            budget=TokenBudget(limit=100),
        )

        self.assertEqual(captured["url"], "https://api.minimax.io/v1/t2a_v2")
        headers = captured["headers"]
        self.assertIsInstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer sk-cp-test")
        body = captured["body"]
        self.assertIsInstance(body, dict)
        self.assertEqual(body["model"], "speech-2.8-turbo")
        self.assertEqual(body["voice_setting"]["voice_id"], "male-qn-qingse")
        self.assertEqual(result.audio, b"hi")
        self.assertEqual(result.content_type, "audio/mpeg")
        self.assertEqual(result.characters, 4)


if __name__ == "__main__":
    unittest.main()
