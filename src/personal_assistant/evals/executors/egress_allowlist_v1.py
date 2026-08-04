"""Deterministic probes for the ADR-004 egress allowlist (layer A).

Each scenario runs the production enforcement path hermetically: adapter
constructors receive a recording fake ``urlopen`` that never touches the
network, so an allowed target is proven to complete one request while a
blocked target is proven to fail before any connection is opened. The
startup scenario builds ``AppSettings`` directly to prove fail-closed
coverage validation rejects an explicit allowlist that misses an enabled
provider.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from personal_assistant.adapters.outbound.egress import (
    EgressAllowlist,
    EgressNotAllowedError,
)
from personal_assistant.adapters.outbound.llm.anthropic import (
    AnthropicCompatibleLLMProvider,
)
from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
)
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import LLMRequest
from personal_assistant.application.services.prompts import (
    PromptTemplate,
    StaticPromptCatalog,
)
from personal_assistant.evals.schema import StrictModel
from personal_assistant.infrastructure.config import AppSettings

ALLOWED_HOST = "llm-eval.example"
BLOCKED_HOST = "blocked-eval.example"
TEST_TELEGRAM_TOKEN = "test_telegram_token_placeholder"


class InputModel(StrictModel):
    scenario: Literal[
        "llm-allowlisted",
        "llm-not-covered",
        "telegram-not-covered",
        "startup-not-covered",
    ]


class ExpectedModel(StrictModel):
    outcome: Literal["allowed", "blocked", "startup_rejected"]
    code: str | None
    urlopenCalls: int = Field(ge=0)


class _RecordingUrlopen:
    """Fake urlopen that only records calls and returns a canned LLM reply."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, req: object, timeout: float) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(
            {
                "model": "eval-model",
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _prompt_catalog() -> StaticPromptCatalog:
    return StaticPromptCatalog(
        {
            "llm_json_system": PromptTemplate(
                prompt_id="llm_json_system",
                version="eval",
                template="JSON_SYSTEM schema=$schema_name",
                required_variables=("schema_name",),
            )
        }
    )


def _blocked_result(urlopen: _RecordingUrlopen, error: EgressNotAllowedError) -> dict[str, object]:
    return {
        "outcome": "blocked",
        "code": error.response.error.code.value,
        "urlopenCalls": urlopen.calls,
    }


def _llm_scenario(*, covered: bool) -> dict[str, object]:
    urlopen = _RecordingUrlopen()
    host = ALLOWED_HOST if covered else BLOCKED_HOST
    allowlist = EgressAllowlist.from_entries({ALLOWED_HOST})
    try:
        provider = AnthropicCompatibleLLMProvider(
            api_key="test_llm_api_key_placeholder",
            base_url=f"https://{host}",
            model="eval-model",
            prompt_catalog=_prompt_catalog(),
            urlopen=urlopen,
            egress_allowlist=allowlist,
        )
        provider.complete(
            LLMRequest(prompt="extrae", schema_name="reminder_extraction"),
            budget=TokenBudget(limit=1000),
        )
    except EgressNotAllowedError as error:
        return _blocked_result(urlopen, error)
    return {"outcome": "allowed", "code": None, "urlopenCalls": urlopen.calls}


def _telegram_scenario() -> dict[str, object]:
    urlopen = _RecordingUrlopen()
    allowlist = EgressAllowlist.from_entries({"unrelated-eval.example"})
    try:
        TelegramBotApiClient(
            token=TEST_TELEGRAM_TOKEN,
            egress_allowlist=allowlist,
        )
    except EgressNotAllowedError as error:
        return _blocked_result(urlopen, error)
    return {"outcome": "allowed", "code": None, "urlopenCalls": urlopen.calls}


def _startup_scenario() -> dict[str, object]:
    try:
        AppSettings(
            telegram_bot_token=TEST_TELEGRAM_TOKEN,
            egress_allowed_hosts=frozenset({"unrelated-eval.example"}),
        )
    except ValueError:
        return {"outcome": "startup_rejected", "code": None, "urlopenCalls": 0}
    return {"outcome": "allowed", "code": None, "urlopenCalls": 0}


def execute(value: InputModel) -> dict[str, object]:
    if value.scenario == "llm-allowlisted":
        return _llm_scenario(covered=True)
    if value.scenario == "llm-not-covered":
        return _llm_scenario(covered=False)
    if value.scenario == "telegram-not-covered":
        return _telegram_scenario()
    return _startup_scenario()
