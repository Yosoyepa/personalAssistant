"""Deterministic probes for the ADR-004 layer A egress allowlist."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Self

from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.egress import (
    DEFAULT_TELEGRAM_API_URL,
    EgressAllowlist,
    EgressNotAllowedError,
    derive_egress_entries,
    require_startup_coverage,
)
from personal_assistant.adapters.outbound.llm.anthropic import (
    AnthropicCompatibleLLMProvider,
)
from personal_assistant.adapters.outbound.llm.minimax import MiniMaxLLMProvider
from personal_assistant.adapters.outbound.notifications.local import (
    LocalNotificationTool,
)
from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
)
from personal_assistant.adapters.outbound.transcription.openai_compatible import (
    OpenAICompatibleTranscriptionProvider,
)
from personal_assistant.adapters.outbound.tts.minimax import MiniMaxTTSProvider
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import LLMRequest
from personal_assistant.application.services.prompts import (
    PromptTemplate,
    StaticPromptCatalog,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.infrastructure.config import AppSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "personal_assistant"


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


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _forbidden_urlopen(req: object, timeout: float) -> object:
    raise AssertionError("a connection was opened for a non-allowlisted target")


class EgressEntryParsingTests(unittest.TestCase):
    def test_bare_hostname_implies_https(self) -> None:
        allowlist = EgressAllowlist.from_entries({"api.telegram.org"})
        self.assertTrue(allowlist.allows("https://api.telegram.org/bot123/sendMessage"))
        self.assertFalse(allowlist.allows("http://api.telegram.org/bot123/sendMessage"))

    def test_explicit_scheme_and_case_normalization(self) -> None:
        allowlist = EgressAllowlist.from_entries({"HTTPS://API.MiniMax.IO"})
        self.assertTrue(allowlist.allows("https://api.minimax.io/v1/t2a_v2"))

    def test_exact_hostname_only_no_subdomains(self) -> None:
        allowlist = EgressAllowlist.from_entries({"api.telegram.org"})
        self.assertFalse(allowlist.allows("https://sub.api.telegram.org"))
        self.assertFalse(allowlist.allows("https://api.telegram.org.evil.example"))

    def test_invalid_entries_are_rejected(self) -> None:
        for entry in (
            "",
            "   ",
            "*",
            "*.example.com",
            "example.com/path",
            "example.com:8443",
            "user@example.com",
            "https://user:password@example.com",
        ):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                EgressAllowlist.from_entries({entry})

    def test_empty_allowlist_denies_everything(self) -> None:
        allowlist = EgressAllowlist.from_entries(set())
        self.assertFalse(allowlist.allows("https://api.telegram.org"))
        self.assertEqual(allowlist.audit_hosts(), ())


class EgressRequireTests(unittest.TestCase):
    def test_require_raises_domain_owned_guardrail_error(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError) as caught:
            allowlist.require("https://blocked.example/v1/messages")
        error = caught.exception
        self.assertIsInstance(error, AssistantError)
        self.assertEqual(error.response.error.code, ErrorCode.GUARDRAIL_BLOCKED)

    def test_error_payload_never_carries_url_credentials(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError) as caught:
            allowlist.require("https://user:supersecret@blocked.example/path")
        payload = json.dumps(caught.exception.response.model_dump(mode="json"))
        self.assertNotIn("supersecret", payload)
        self.assertNotIn("user:supersecret", payload)

    def test_invalid_or_schemeless_urls_fail_closed(self) -> None:
        allowlist = EgressAllowlist.from_entries({"api.telegram.org"})
        for url in ("", "not a url", "api.telegram.org", "//api.telegram.org"):
            with self.subTest(url=url):
                self.assertFalse(allowlist.allows(url))
                with self.assertRaises(EgressNotAllowedError):
                    allowlist.require(url)


class EgressAuditTests(unittest.TestCase):
    def test_audit_hosts_are_sorted_unique_hostnames_only(self) -> None:
        allowlist = EgressAllowlist.from_entries(
            {
                "https://api.minimax.io",
                "api.telegram.org",
                "https://api.groq.com",
            }
        )
        self.assertEqual(
            allowlist.audit_hosts(),
            ("api.groq.com", "api.minimax.io", "api.telegram.org"),
        )
        for host in allowlist.audit_hosts():
            self.assertNotIn("://", host)
            self.assertNotIn("/", host)


class EgressDerivationTests(unittest.TestCase):
    def test_derives_entries_from_configured_base_urls(self) -> None:
        entries = derive_egress_entries(
            llm_base_url="https://api.minimax.io/anthropic",
            transcription_base_url="https://api.groq.com/openai",
            tts_base_url="https://api.minimax.io",
            telegram_bot_token_configured=True,
        )
        allowlist = EgressAllowlist.from_entries(entries)
        self.assertTrue(allowlist.allows("https://api.minimax.io/v1/t2a_v2"))
        self.assertTrue(allowlist.allows("https://api.groq.com/openai/v1/audio"))
        self.assertTrue(allowlist.allows(DEFAULT_TELEGRAM_API_URL))

    def test_skips_blank_and_invalid_base_urls(self) -> None:
        entries = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url="   ",
            tts_base_url="not a url",
            telegram_bot_token_configured=False,
        )
        self.assertEqual(entries, frozenset())

    def test_telegram_host_requires_a_configured_token(self) -> None:
        without_token = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url=None,
            tts_base_url=None,
            telegram_bot_token_configured=False,
        )
        with_token = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url=None,
            tts_base_url=None,
            telegram_bot_token_configured=True,
        )
        self.assertNotIn(DEFAULT_TELEGRAM_API_URL, without_token)
        self.assertIn(DEFAULT_TELEGRAM_API_URL, with_token)

    def test_whatsapp_hosts_require_a_configured_token(self) -> None:
        without_token = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url=None,
            tts_base_url=None,
            telegram_bot_token_configured=False,
            whatsapp_access_token_configured=False,
        )
        with_token = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url=None,
            tts_base_url=None,
            telegram_bot_token_configured=False,
            whatsapp_access_token_configured=True,
        )
        self.assertNotIn("https://graph.facebook.com", without_token)
        self.assertNotIn("https://lookaside.fbsbx.com", without_token)
        self.assertIn("https://graph.facebook.com", with_token)
        self.assertIn("https://lookaside.fbsbx.com", with_token)


class StartupCoverageTests(unittest.TestCase):
    def test_uncovered_required_target_fails_closed(self) -> None:
        allowlist = EgressAllowlist.from_entries({"api.telegram.org"})
        with self.assertRaises(ValueError) as caught:
            require_startup_coverage(
                allowlist, {"LLM_PROVIDER": "https://api.minimax.io/anthropic"}
            )
        message = str(caught.exception)
        self.assertIn("EGRESS_ALLOWED_HOSTS", message)
        self.assertIn("LLM_PROVIDER", message)
        self.assertIn("api.minimax.io", message)

    def test_covered_targets_pass(self) -> None:
        allowlist = EgressAllowlist.from_entries({"api.telegram.org"})
        require_startup_coverage(
            allowlist, {"TELEGRAM_BOT_TOKEN": DEFAULT_TELEGRAM_API_URL}
        )


class AppSettingsEgressTests(unittest.TestCase):
    def test_empty_explicit_derives_from_provider_config(self) -> None:
        settings = AppSettings(
            llm_provider="minimax",
            llm_api_key="key",
            llm_base_url="https://api.minimax.io/anthropic",
            telegram_bot_token="token",
        )
        allowlist = EgressAllowlist.from_entries(settings.egress_allowed_hosts)
        self.assertTrue(allowlist.allows("https://api.minimax.io/anthropic"))
        self.assertTrue(allowlist.allows(DEFAULT_TELEGRAM_API_URL))

    def test_explicit_override_wins_over_derivation(self) -> None:
        explicit = frozenset({"https://api.telegram.org", "api.minimax.io"})
        settings = AppSettings(
            llm_provider="minimax",
            llm_api_key="key",
            llm_base_url="https://api.minimax.io/anthropic",
            telegram_bot_token="token",
            egress_allowed_hosts=explicit,
        )
        self.assertEqual(settings.egress_allowed_hosts, explicit)

    def test_startup_fails_closed_when_override_misses_enabled_provider(self) -> None:
        with self.assertRaises(ValueError) as caught:
            AppSettings(
                llm_provider="minimax",
                llm_api_key="key",
                llm_base_url="https://api.minimax.io/anthropic",
                egress_allowed_hosts=frozenset({"api.telegram.org"}),
            )
        self.assertIn("LLM_PROVIDER", str(caught.exception))

    def test_startup_fails_closed_for_transcription_and_tts(self) -> None:
        with self.assertRaises(ValueError) as caught_transcription:
            AppSettings(
                transcription_provider="openai_compatible",
                transcription_api_key="key",
                transcription_base_url="https://api.groq.com/openai",
                egress_allowed_hosts=frozenset({"api.telegram.org"}),
            )
        self.assertIn("TRANSCRIPTION_PROVIDER", str(caught_transcription.exception))
        with self.assertRaises(ValueError) as caught_tts:
            AppSettings(
                tts_provider="minimax",
                tts_api_key="key",
                tts_base_url="https://api.minimax.io",
                egress_allowed_hosts=frozenset({"api.telegram.org"}),
            )
        self.assertIn("TTS_PROVIDER", str(caught_tts.exception))

    def test_startup_fails_closed_when_override_misses_telegram(self) -> None:
        with self.assertRaises(ValueError) as caught:
            AppSettings(
                telegram_bot_token="token",
                egress_allowed_hosts=frozenset({"api.minimax.io"}),
            )
        self.assertIn("TELEGRAM_BOT_TOKEN", str(caught.exception))

    def test_disabled_providers_need_no_coverage(self) -> None:
        settings = AppSettings(egress_allowed_hosts=frozenset({"unrelated.example"}))
        self.assertEqual(
            settings.egress_allowed_hosts, frozenset({"unrelated.example"})
        )

    def test_invalid_explicit_entry_fails_at_startup(self) -> None:
        with self.assertRaises(ValueError):
            AppSettings(egress_allowed_hosts=frozenset({"*.example.com"}))


class AdapterEgressEnforcementTests(unittest.TestCase):
    def test_anthropic_llm_raises_before_opening_a_connection(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError):
            AnthropicCompatibleLLMProvider(
                api_key="key",
                base_url="https://blocked.example",
                model="model",
                prompt_catalog=_prompt_catalog(),
                urlopen=_forbidden_urlopen,
                egress_allowlist=allowlist,
            )

    def test_minimax_llm_forwards_egress_validation(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError):
            MiniMaxLLMProvider(
                api_key="key",
                base_url="https://blocked.example/anthropic",
                model="model",
                prompt_catalog=_prompt_catalog(),
                urlopen=_forbidden_urlopen,
                egress_allowlist=allowlist,
            )

    def test_transcription_raises_before_opening_a_connection(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError):
            OpenAICompatibleTranscriptionProvider(
                api_key="key",
                base_url="https://blocked.example",
                model="model",
                urlopen=_forbidden_urlopen,
                egress_allowlist=allowlist,
            )

    def test_tts_raises_before_opening_a_connection(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError):
            MiniMaxTTSProvider(
                api_key="key",
                base_url="https://blocked.example",
                model="model",
                urlopen=_forbidden_urlopen,
                egress_allowlist=allowlist,
            )

    def test_telegram_client_raises_before_opening_a_connection(self) -> None:
        allowlist = EgressAllowlist.from_entries({"allowed.example"})
        with self.assertRaises(EgressNotAllowedError):
            TelegramBotApiClient(
                token="token",
                egress_allowlist=allowlist,
            )

    def test_allowlisted_llm_still_completes_normally(self) -> None:
        allowlist = EgressAllowlist.from_entries({"llm.example"})

        def fake_urlopen(req: object, timeout: float) -> _FakeResponse:
            return _FakeResponse(
                {
                    "model": "claude-test",
                    "content": [{"type": "text", "text": '{"ok": true}'}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            )

        provider = AnthropicCompatibleLLMProvider(
            api_key="key",
            base_url="https://llm.example",
            model="claude-test",
            prompt_catalog=_prompt_catalog(),
            urlopen=fake_urlopen,
            egress_allowlist=allowlist,
        )
        result = provider.complete(
            LLMRequest(prompt="extrae", schema_name="reminder_extraction"),
            budget=TokenBudget(limit=1000),
        )
        self.assertEqual(result.data["ok"], True)

    def test_adapters_without_allowlist_keep_previous_behavior(self) -> None:
        provider = MiniMaxTTSProvider(
            api_key="key",
            base_url="https://tts.example",
            model="model",
            urlopen=_forbidden_urlopen,
        )
        self.assertEqual(provider.provider, "minimax")
        client = TelegramBotApiClient(token="token")
        self.assertIsNotNone(client)


class LocalToolEgressTests(unittest.TestCase):
    def test_local_tools_need_no_egress_entries(self) -> None:
        empty_allowlist = EgressAllowlist.from_entries(set())
        self.assertEqual(empty_allowlist.audit_hosts(), ())
        calendar = LocalCalendarTool()
        notifications = LocalNotificationTool()
        self.assertEqual(calendar.permission_tier.value, "P3")
        self.assertEqual(notifications.permission_tier.value, "P5")


class AdapterBoundaryProbeTests(unittest.TestCase):
    def test_no_http_client_construction_outside_adapter_boundary(self) -> None:
        forbidden_needles = ("urllib_request", "httpx", "requests.")
        offenders: list[str] = []
        for layer in (SRC_ROOT / "domain", SRC_ROOT / "application"):
            for file in sorted(layer.rglob("*.py")):
                if "__pycache__" in file.parts:
                    continue
                text = file.read_text(encoding="utf-8")
                for needle in forbidden_needles:
                    if needle in text:
                        offenders.append(f"{file.relative_to(PROJECT_ROOT)}: {needle}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
