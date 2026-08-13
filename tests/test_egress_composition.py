"""Composition-root wiring tests for the ADR-004 egress allowlist."""

from __future__ import annotations

import unittest
from unittest import mock

from personal_assistant.adapters.outbound.egress import (
    DEFAULT_TELEGRAM_API_URL,
    EgressNotAllowedError,
)
from personal_assistant.adapters.outbound.llm.anthropic import (
    AnthropicCompatibleLLMProvider,
)
from personal_assistant.adapters.outbound.llm.minimax import MiniMaxLLMProvider
from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
)
from personal_assistant.adapters.outbound.transcription.openai_compatible import (
    OpenAICompatibleTranscriptionProvider,
)
from personal_assistant.adapters.outbound.tts.minimax import MiniMaxTTSProvider
from personal_assistant.infrastructure.bootstrap import (
    build_egress_allowlist,
    build_llm_provider,
    build_transcription_provider,
    build_tts_provider,
    egress_audit_record,
    log_egress_audit,
)
from personal_assistant.infrastructure.config import AppSettings


def _full_settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "llm_provider": "minimax",
        "llm_api_key": "sentinel-llm-key",
        "llm_base_url": "https://api.minimax.io/anthropic",
        "llm_model": "MiniMax-M3",
        "transcription_provider": "openai_compatible",
        "transcription_api_key": "sentinel-transcription-key",
        "transcription_base_url": "https://api.groq.com/openai",
        "transcription_model": "whisper-large-v3-turbo",
        "tts_provider": "minimax",
        "tts_api_key": "sentinel-tts-key",
        "tts_base_url": "https://api.minimax.io",
        "tts_model": "speech-2.8-turbo",
        "telegram_bot_token": "123456789:sentinel-telegram-token",
    }
    base.update(overrides)
    return AppSettings(**base)  # type: ignore[arg-type]  # reason: dict de settings centinela; AppSettings valida los tipos en runtime


class CompositionEgressWiringTests(unittest.TestCase):
    def test_llm_providers_are_built_with_the_effective_allowlist(self) -> None:
        settings = _full_settings()
        minimax = build_llm_provider(settings)
        self.assertIsInstance(minimax, MiniMaxLLMProvider)
        anthropic_settings = _full_settings(
            llm_provider="anthropic_compatible",
            llm_base_url="https://claude.example",
        )
        anthropic = build_llm_provider(anthropic_settings)
        self.assertIsInstance(anthropic, AnthropicCompatibleLLMProvider)

    def test_transcription_and_tts_are_built_with_the_allowlist(self) -> None:
        settings = _full_settings()
        self.assertIsInstance(
            build_transcription_provider(settings),
            OpenAICompatibleTranscriptionProvider,
        )
        self.assertIsInstance(build_tts_provider(settings), MiniMaxTTSProvider)

    def test_telegram_client_accepts_the_derived_allowlist(self) -> None:
        settings = _full_settings()
        client = TelegramBotApiClient(
            token=settings.telegram_bot_token or "",
            egress_allowlist=build_egress_allowlist(settings),
        )
        self.assertIsNotNone(client)

    def test_composition_fails_closed_on_uncovered_adapter_target(self) -> None:
        # AppSettings validation already rejects this state; this probe proves
        # the adapter boundary itself stays fail-closed when a caller builds
        # an adapter with an allowlist that does not cover its target.
        settings = _full_settings(
            egress_allowed_hosts=frozenset(
                {
                    "api.minimax.io",
                    "api.groq.com",
                    "api.telegram.org",
                }
            )
        )
        allowlist = build_egress_allowlist(settings)
        with self.assertRaises(EgressNotAllowedError):
            AnthropicCompatibleLLMProvider(
                api_key="key",
                base_url="https://uncovered.example",
                model="model",
                prompt_catalog=mock.Mock(),
                egress_allowlist=allowlist,
            )

    def test_disabled_providers_return_none(self) -> None:
        settings = AppSettings()
        self.assertIsNone(build_llm_provider(settings))
        self.assertIsNone(build_transcription_provider(settings))
        self.assertIsNone(build_tts_provider(settings))


class EgressAuditRecordTests(unittest.TestCase):
    def test_audit_record_contains_hostnames_only(self) -> None:
        settings = _full_settings()
        record = egress_audit_record(settings)
        self.assertTrue(record.startswith("egress allowlist hosts: "))
        self.assertIn("api.minimax.io", record)
        self.assertIn("api.groq.com", record)
        self.assertIn("api.telegram.org", record)
        self.assertNotIn("://", record)

    def test_audit_record_never_carries_configured_credentials(self) -> None:
        settings = _full_settings()
        record = egress_audit_record(settings)
        for sentinel in (
            "sentinel-llm-key",
            "sentinel-transcription-key",
            "sentinel-tts-key",
            "sentinel-telegram-token",
        ):
            self.assertNotIn(sentinel, record)

    def test_audit_record_marks_empty_allowlist(self) -> None:
        self.assertEqual(
            egress_audit_record(AppSettings()),
            "egress allowlist hosts: (none)",
        )

    def test_allowlist_covers_telegram_default_url(self) -> None:
        settings = _full_settings()
        self.assertTrue(
            build_egress_allowlist(settings).allows(DEFAULT_TELEGRAM_API_URL)
        )

    def test_log_egress_audit_emits_the_record(self) -> None:
        settings = _full_settings()
        with self.assertLogs("personal_assistant.egress", level="INFO") as captured:
            log_egress_audit(settings)
        self.assertEqual(len(captured.records), 1)
        message = captured.records[0].getMessage()
        self.assertIn("api.telegram.org", message)
        self.assertNotIn("sentinel-telegram-token", message)


if __name__ == "__main__":
    unittest.main()
