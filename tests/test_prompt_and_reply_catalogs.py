from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from personal_assistant.application.dto.channels import ChannelName, NormalizedMessage
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import LLMResult
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.services.prompts import (
    LLM_JSON_SYSTEM_PROMPT_ID,
    PromptTemplate,
    StaticPromptCatalog,
)
from personal_assistant.application.use_cases.runtime import LocalAgentRuntime
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.prompts import build_prompt_catalog


NOW = datetime(2026, 6, 20, 12, tzinfo=UTC)


def _principal() -> Principal:
    return Principal.for_test(
        principal_id="telegram-user-1",
        tenant_id="tenant-a",
        permission_tier=PermissionTier.P5,
    )


def _message(text: str, message_id: str = "42") -> NormalizedMessage:
    principal = _principal()
    return NormalizedMessage(
        channel=ChannelName.telegram,
        actor_id=principal.principal_id,
        conversation_id="chat-1",
        message_id=message_id,
        text=text,
    )


def _write_prompt_registry(
    root: Path, prompt_id: str, *, version: str, template: str
) -> None:
    prompt_path = root / prompt_id / f"{version}.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text(template, encoding="utf-8")
    (root / "registry.json").write_text(
        json.dumps(
            {
                "prompts": {
                    prompt_id: {
                        "version": version,
                        "path": f"{prompt_id}/{version}.md",
                        "required_variables": ["text"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _catalog_with(
    prompt_id: str, template: str, *required_variables: str
) -> StaticPromptCatalog:
    return StaticPromptCatalog(
        {
            prompt_id: PromptTemplate(
                prompt_id=prompt_id,
                version="test-default",
                template=template,
                required_variables=required_variables,
            )
        }
    )


def test_static_prompt_catalog_renders_injected_default_template() -> None:
    catalog = _catalog_with(
        "conversation_intent",
        "DEFAULT_PROMPT text=$text allowed=$allowed_intents",
        "text",
        "allowed_intents",
    )

    rendered = catalog.render(
        "conversation_intent",
        {
            "text": "recordame pagar",
            "allowed_intents": ["reminder.create", "unsupported"],
        },
    )

    assert rendered.prompt_id == "conversation_intent"
    assert rendered.version == "test-default"
    assert "DEFAULT_PROMPT" in rendered.text
    assert "recordame pagar" in rendered.text
    assert '["reminder.create", "unsupported"]' in rendered.text


def test_static_prompt_catalog_rejects_missing_required_variables() -> None:
    catalog = _catalog_with("reminder_extraction", "text=$text now=$now", "text", "now")

    with pytest.raises(KeyError, match="missing prompt variables"):
        catalog.render("reminder_extraction", {"text": "recordame pagar"})


def test_filesystem_prompt_catalog_loads_versioned_prompt_files(tmp_path: Path) -> None:
    _write_prompt_registry(
        tmp_path,
        "conversation_intent",
        version="v7",
        template="FROM_VERSIONED_FILE\ntext=$text",
    )

    rendered = build_prompt_catalog(tmp_path).render(
        "conversation_intent", {"text": "hola"}
    )

    assert rendered.prompt_id == "conversation_intent"
    assert rendered.version == "v7"
    assert rendered.text == "FROM_VERSIONED_FILE\ntext=hola"


def test_filesystem_prompt_catalog_rejects_malformed_registry(tmp_path: Path) -> None:
    (tmp_path / "registry.json").write_text(json.dumps({"items": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="prompts object"):
        build_prompt_catalog(tmp_path)


def test_repository_prompt_registry_covers_runtime_llm_prompts() -> None:
    catalog = build_prompt_catalog()

    intent = catalog.render(
        "conversation_intent",
        {
            "allowed_intents": ["reminder.create", "unsupported"],
            "now": NOW.isoformat(),
            "timezone": "America/Bogota",
            "text": "recordame pagar",
        },
    )
    reminder = catalog.render(
        "reminder_extraction",
        {
            "now": NOW.isoformat(),
            "timezone": "America/Bogota",
            "text": "recordame en 2 minutos pagar",
        },
    )
    transcription = catalog.render("telegram_voice_transcription", {})
    json_system = catalog.render(
        LLM_JSON_SYSTEM_PROMPT_ID, {"schema_name": "reminder_extraction"}
    )

    assert intent.version == "v1"
    assert "recordame pagar" in intent.text
    assert reminder.version == "v1"
    assert "recordame en 2 minutos pagar" in reminder.text
    assert transcription.version == "v1"
    assert "Transcribe mensajes de voz" in transcription.text
    assert json_system.version == "v1"
    assert "schema_name=reminder_extraction" in json_system.text


def test_repository_reply_catalog_loads_user_facing_copy_from_locale_file() -> None:
    catalog_path = Path(__file__).resolve().parents[1] / "locales" / "es.json"
    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    replies = AssistantReplies()

    assert replies.start() == raw_catalog["start"]
    assert replies.help() == "\n".join(raw_catalog["help"])
    assert replies.status(
        pending_count=1, state_count=2, event_count=3, outbox_count=4
    ) == raw_catalog["status"].format(
        pending_count=1, state_count=2, event_count=3, outbox_count=4
    )
    assert replies.reminder_needs_approval("clase") == raw_catalog[
        "reminder_needs_approval"
    ].format(title="clase")
    assert replies.runtime_request_received() == raw_catalog["runtime_request_received"]
    assert replies.approval_command_hint("apr-1") == raw_catalog[
        "approval_command_hint"
    ].format(approval_id="apr-1")
    assert (
        replies.approval_reason_calendar_create_event()
        == raw_catalog["approval_reason_calendar_create_event"]
    )
    assert replies.reminder_notification_body("clase") == raw_catalog[
        "reminder_notification_body"
    ].format(title="clase")
    assert replies.approval_failed() == raw_catalog["approval_failed"]
    assert replies.approval_cancel_failed() == raw_catalog["approval_cancel_failed"]


class CommandReplyDefaults(AssistantReplies):
    def help(self) -> str:
        return "HELP_FROM_INJECTED_REPLY_DEFAULTS"

    def unsupported(self) -> str:
        return "UNSUPPORTED_FROM_INJECTED_REPLY_DEFAULTS"

    def approval_command_hint(self, approval_id: str) -> str:
        return f"APPROVAL_HINT_FROM_INJECTED_REPLY_DEFAULTS:{approval_id}"


def test_command_router_uses_injected_reply_defaults() -> None:
    container = build_container()
    container.commands.replies = CommandReplyDefaults()
    principal = _principal()

    help_result = container.commands.handle(
        principal,
        _message("/help"),
        now=NOW,
        timezone="America/Bogota",
    )
    unsupported_result = container.commands.handle(
        principal,
        _message("/nope", message_id="43"),
        now=NOW,
        timezone="America/Bogota",
    )

    assert help_result.reply == "HELP_FROM_INJECTED_REPLY_DEFAULTS"
    assert unsupported_result.reply == "UNSUPPORTED_FROM_INJECTED_REPLY_DEFAULTS"


def test_command_router_uses_injected_approval_hint() -> None:
    container = build_container()
    container.commands.replies = CommandReplyDefaults()

    result = container.commands.handle(
        _principal(),
        _message("recuérdame clase el martes a las 5"),
        now=NOW,
        timezone="America/Bogota",
    )

    assert result.approval_id is not None
    assert (
        f"APPROVAL_HINT_FROM_INJECTED_REPLY_DEFAULTS:{result.approval_id}"
        in result.reply
    )


def test_local_agent_runtime_uses_injected_reply_defaults() -> None:
    class RuntimeReplies(AssistantReplies):
        def runtime_request_received(self) -> str:
            return "RUNTIME_REPLY_FROM_INJECTED_REPLY_DEFAULTS"

    result = LocalAgentRuntime(replies=RuntimeReplies()).run(
        "estado",
        principal=_principal(),
        budget=TokenBudget(limit=100),
    )

    assert result.reply == "RUNTIME_REPLY_FROM_INJECTED_REPLY_DEFAULTS"


class ReminderReplyDefaults(AssistantReplies):
    def reminder_needs_datetime(self) -> str:
        return "NEEDS_DATETIME_FROM_INJECTED_REPLY_DEFAULTS"

    def reminder_needs_approval(self, title: str) -> str:
        return f"NEEDS_APPROVAL_FROM_INJECTED_REPLY_DEFAULTS:{title}"


def test_reminder_workflow_uses_injected_reply_defaults() -> None:
    container = build_container()
    container.reminder_workflow.replies = ReminderReplyDefaults()
    principal = _principal()

    needs_datetime = container.reminder_workflow.run(
        principal,
        ReminderWorkflowInput(
            message_id="missing-time",
            conversation_id="chat-1",
            text="recordame pagar",
            recipient="chat-1",
            now=NOW,
            approval=None,
        ),
    )
    needs_approval = container.reminder_workflow.run(
        principal,
        ReminderWorkflowInput(
            message_id="needs-approval",
            conversation_id="chat-1",
            text="recordame clase el martes a las 5",
            recipient="chat-1",
            now=NOW,
            approval=None,
        ),
    )

    assert needs_datetime.reply == "NEEDS_DATETIME_FROM_INJECTED_REPLY_DEFAULTS"
    assert needs_approval.reply.startswith(
        "NEEDS_APPROVAL_FROM_INJECTED_REPLY_DEFAULTS:"
    )


class CapturingIntentLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, request: Any, *, budget: TokenBudget) -> LLMResult:
        self.prompts.append(request.prompt)
        return LLMResult(
            provider="fake",
            model="fake-router",
            data={"kind": "unsupported", "confidence": 0.99, "reminder_text": None},
            input_tokens=4,
            output_tokens=2,
        )


class CapturingReminderLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, request: Any, *, budget: TokenBudget) -> LLMResult:
        self.prompts.append(request.prompt)
        return LLMResult(
            provider="fake",
            model="fake-reminder",
            data={
                "is_reminder": True,
                "title": "almorzar con Ana",
                "starts_at": "2026-06-20T15:33:00+00:00",
                "confidence": 0.91,
            },
            input_tokens=8,
            output_tokens=4,
        )


def test_command_router_renders_intent_prompt_from_injected_catalog() -> None:
    llm = CapturingIntentLLM()
    container = build_container(llm=llm)
    container.commands.prompt_catalog = _catalog_with(
        "conversation_intent",
        "CATALOG_INTENT_PROMPT text=$text now=$now timezone=$timezone allowed=$allowed_intents",
        "text",
        "now",
        "timezone",
        "allowed_intents",
    )

    container.commands.handle(
        _principal(),
        _message("que puedes hacer"),
        now=NOW,
        timezone="America/Bogota",
    )

    assert len(llm.prompts) == 1
    assert "CATALOG_INTENT_PROMPT" in llm.prompts[0]
    assert "que puedes hacer" in llm.prompts[0]


def test_reminder_workflow_renders_extraction_prompt_from_injected_catalog() -> None:
    llm = CapturingReminderLLM()
    container = build_container(llm=llm)
    container.reminder_workflow.prompt_catalog = _catalog_with(
        "reminder_extraction",
        "CATALOG_REMINDER_PROMPT text=$text now=$now timezone=$timezone",
        "text",
        "now",
        "timezone",
    )

    container.reminder_workflow.run(
        _principal(),
        ReminderWorkflowInput(
            message_id="llm-catalog",
            conversation_id="chat-1",
            text="deja lo de almorzar con Ana a las tres treinta y tres",
            recipient="chat-1",
            now=NOW,
            approval=None,
        ),
    )

    assert len(llm.prompts) == 1
    assert "CATALOG_REMINDER_PROMPT" in llm.prompts[0]
    assert "almorzar con Ana" in llm.prompts[0]


def test_filesystem_reply_catalog_loads_versioned_reply_files(tmp_path: Path) -> None:
    reply_path = tmp_path / "help" / "v2.md"
    reply_path.parent.mkdir(parents=True)
    reply_path.write_text("HELP_FROM_VERSIONED_REPLY_FILE", encoding="utf-8")
    (tmp_path / "registry.json").write_text(
        json.dumps(
            {
                "replies": {
                    "help": {
                        "version": "v2",
                        "path": "help/v2.md",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    from personal_assistant.infrastructure.replies import build_reply_catalog

    replies = AssistantReplies.from_catalog(build_reply_catalog(tmp_path))  # type: ignore[attr-defined]

    assert replies.help() == "HELP_FROM_VERSIONED_REPLY_FILE"
