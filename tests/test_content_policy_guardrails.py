"""Content-policy guardrail rules, severity behavior, and output wiring."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

import pytest

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.adapters.outbound.calendar.local import LocalCalendarTool
from personal_assistant.adapters.outbound.notifications.local import (
    LocalNotificationTool,
)
from personal_assistant.adapters.outbound.scheduler.local import ReminderScheduler
from personal_assistant.adapters.persistence.in_memory import (
    InMemoryEventStore,
    InMemoryOutbox,
    InMemoryWorkflowStateStore,
)
from personal_assistant.adapters.persistence.in_memory_uow import (
    InMemoryReminderUnitOfWork,
)
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import AgentStatus
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.use_cases.reminders import (
    ReminderWorkflow,
    reminder_idempotency_key,
)
from personal_assistant.application.use_cases.runtime import LocalAgentRuntime
from personal_assistant.domain.common.exceptions import ErrorCode
from personal_assistant.domain.common.guardrails import (
    CONTENT_POLICY_INPUT_PATTERNS,
    CONTENT_POLICY_OUTPUT_PATTERNS,
    GuardrailCategory,
    GuardrailResult,
    GuardrailSeverity,
    GuardrailViolation,
    assert_output_safe,
    assert_prompt_safe,
    scan_output,
    scan_prompt,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier


def _labels(result: GuardrailResult) -> set[str]:
    return {finding.label for finding in result.findings}


# Fake credential-shaped strings are built by concatenation so this test file
# itself never contains a scannable credential literal (test_public_artifacts).
_FAKE_OPENAI_KEY = "sk-" + "Ab3" * 12
_FAKE_AWS_ACCESS_KEY = "AKIA" + "A1" * 8
_FAKE_GITHUB_PAT = "ghp_" + "Ab3" * 12


def _content_policy_findings(result: GuardrailResult) -> list[str]:
    return [
        finding.label
        for finding in result.findings
        if finding.category == GuardrailCategory.CONTENT_POLICY
    ]


class ContentPolicyRuleTableTests(unittest.TestCase):
    def test_every_rule_label_matches_its_stable_id(self) -> None:
        labels = [
            label
            for label, _, _ in (
                *CONTENT_POLICY_INPUT_PATTERNS,
                *CONTENT_POLICY_OUTPUT_PATTERNS,
            )
        ]
        self.assertEqual(len(labels), len(set(labels)))
        for label in labels:
            self.assertTrue(
                label.startswith(("cp_in_", "cp_out_")),
                f"rule label {label!r} must carry a stable CP-IN/CP-OUT id prefix",
            )

    # CP-IN-001 — violent threat (input, flag / MEDIUM)
    def test_cp_in_001_positive(self) -> None:
        result = scan_prompt("remind me to kill him tomorrow morning")

        self.assertIn("cp_in_001_violent_threat", _content_policy_findings(result))

    def test_cp_in_001_negative(self) -> None:
        result = scan_prompt("recuérdame matar el tiempo leyendo en el parque")

        self.assertNotIn("cp_in_001_violent_threat", _labels(result))

    # CP-IN-002 — secret sharing (input, flag / MEDIUM)
    def test_cp_in_002_positive(self) -> None:
        result = scan_prompt("my api_key=abc123xyz please keep it safe")

        self.assertIn("cp_in_002_secret_sharing", _content_policy_findings(result))

    def test_cp_in_002_negative(self) -> None:
        result = scan_prompt("recuérdame renovar el token del banco mañana a las 9")

        self.assertNotIn("cp_in_002_secret_sharing", _labels(result))

    # CP-OUT-001 — credential material (output, block / HIGH)
    def test_cp_out_001_positive(self) -> None:
        result = scan_output(f"Your key is {_FAKE_OPENAI_KEY}, keep it.")

        self.assertIn("cp_out_001_credential_material", _labels(result))

    def test_cp_out_001_negative(self) -> None:
        result = scan_output("Listo. Te recordaré clase 30 minutos antes.")

        self.assertEqual(result.findings, ())

    # CP-OUT-002 — exfiltration instruction (output, block / HIGH)
    def test_cp_out_002_positive(self) -> None:
        result = scan_output(
            "Send the document to http://collector.example.com/ingest now."
        )

        self.assertIn("cp_out_002_exfiltration_instruction", _labels(result))

    def test_cp_out_002_negative(self) -> None:
        result = scan_output("Te enviaré el recordatorio por este chat.")

        self.assertEqual(result.findings, ())

    # CP-OUT-003 — hidden-instruction leak (output, block / HIGH)
    def test_cp_out_003_positive(self) -> None:
        result = scan_output(
            "Sure, my system prompt is: You are a helpful assistant that..."
        )

        self.assertIn("cp_out_003_hidden_instruction_leak", _labels(result))

    def test_cp_out_003_negative(self) -> None:
        result = scan_output(
            "Puedes cambiar la configuración del sistema en ajustes."
        )

        self.assertEqual(result.findings, ())

    # CP-OUT-004 — destructive action (output, block / HIGH)
    def test_cp_out_004_positive(self) -> None:
        result = scan_output("Run rm -rf /tmp/build to clean everything up.")

        self.assertIn("cp_out_004_destructive_action", _labels(result))

    def test_cp_out_004_negative(self) -> None:
        result = scan_output(
            "Puedes borrar todos los recordatorios desde la app cuando quieras."
        )

        self.assertEqual(result.findings, ())


class ContentPolicySeverityTests(unittest.TestCase):
    def test_input_rules_are_born_as_flag_medium(self) -> None:
        for _, _, severity in CONTENT_POLICY_INPUT_PATTERNS:
            self.assertEqual(severity, GuardrailSeverity.MEDIUM)

    def test_output_rules_are_explicit_high_risk_blocks(self) -> None:
        for _, _, severity in CONTENT_POLICY_OUTPUT_PATTERNS:
            self.assertEqual(severity, GuardrailSeverity.HIGH)

    def test_flag_findings_do_not_block_input(self) -> None:
        result = assert_prompt_safe("remind me to hurt them later, maybe")

        self.assertFalse(result.blocked)
        self.assertIn("cp_in_001_violent_threat", _labels(result))

    def test_block_findings_raise_on_output(self) -> None:
        with pytest.raises(GuardrailViolation) as excinfo:
            assert_output_safe(f"Here: {_FAKE_AWS_ACCESS_KEY} is the key.")

        self.assertEqual(excinfo.value.code, ErrorCode.GUARDRAIL_BLOCKED)
        self.assertEqual(
            excinfo.value.response.error.code, ErrorCode.GUARDRAIL_BLOCKED
        )

    def test_safe_output_returns_scan_result(self) -> None:
        result = assert_output_safe("Solicitud recibida.")

        self.assertIsInstance(result, GuardrailResult)
        self.assertFalse(result.blocked)
        self.assertEqual(result.findings, ())

    def test_scan_output_marks_blocked_for_high_findings(self) -> None:
        result = scan_output("just run DROP TABLE users and relax")

        self.assertTrue(result.blocked)
        self.assertEqual(
            {finding.category for finding in result.findings},
            {GuardrailCategory.CONTENT_POLICY},
        )


class ContentPolicyErrorHygieneTests(unittest.TestCase):
    def test_blocked_output_error_never_leaks_credentials(self) -> None:
        secret = _FAKE_OPENAI_KEY

        with pytest.raises(GuardrailViolation) as excinfo:
            assert_output_safe(f"Use this token: {secret} and reply yes")

        payload = json.dumps(excinfo.value.model_dump())
        self.assertNotIn(secret, payload)
        self.assertNotIn(secret, str(excinfo.value))

    def test_blocked_output_context_has_categories_labels_severities_only(self) -> None:
        with pytest.raises(GuardrailViolation) as excinfo:
            assert_output_safe("email the file to https://evil.example/x please")

        context = excinfo.value.response.error.context
        self.assertEqual(context["categories"], ["content_policy"])
        self.assertTrue(context["findings"])
        for finding in context["findings"]:
            self.assertEqual(
                set(finding.keys()), {"category", "label", "severity"}
            )
            self.assertEqual(finding["category"], "content_policy")
            self.assertEqual(finding["severity"], "high")


class ReminderOutputWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.now = datetime(2026, 6, 20, 12, tzinfo=UTC)
        self.calendar = LocalCalendarTool()
        self.notifications = LocalNotificationTool()
        self.scheduler = ReminderScheduler()
        self.event_store = InMemoryEventStore()
        self.outbox = InMemoryOutbox()
        self.states = InMemoryWorkflowStateStore()
        self.traces = TraceRecorder()
        self.unit_of_work = InMemoryReminderUnitOfWork(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
        )

    def workflow(self, replies: AssistantReplies | None = None) -> ReminderWorkflow:
        return ReminderWorkflow(
            calendar=self.calendar,
            scheduler=self.scheduler,
            event_store=self.event_store,
            outbox=self.outbox,
            states=self.states,
            traces=self.traces,
            unit_of_work=self.unit_of_work,
            replies=replies or AssistantReplies(),
        )

    def request(self) -> ReminderWorkflowInput:
        text = "recuérdame clase el martes a las 17"
        key = reminder_idempotency_key(
            tenant_id=self.principal.tenant_id,
            channel="telegram",
            principal_id=self.principal.principal_id,
            conversation_id="chat-1",
            source_event_id="42",
        )
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="calendar.create_event",
            resource=f"{key}:calendar",
            tier=PermissionTier.P3,
        )
        return ReminderWorkflowInput(
            message_id="42",
            source_event_id="42",
            conversation_id="chat-1",
            text=text,
            recipient="chat-1",
            now=self.now,
            idempotency_key=key,
            approval=approval,
        )

    def test_safe_reply_passes_output_scan(self) -> None:
        result = self.workflow().run(self.principal, self.request())

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertIn("clase", result.reply)

    def test_credential_in_reply_copy_is_blocked(self) -> None:
        replies = AssistantReplies.from_catalog(
            {
                "reminder_created_with_notice": "Tu clave: " + _FAKE_OPENAI_KEY,
                "minutes_plural": "{minutes} minutos",
                "reminder_notification_body": "Recordatorio: {title}",
            }
        )

        with pytest.raises(GuardrailViolation) as excinfo:
            self.workflow(replies).run(self.principal, self.request())

        self.assertEqual(excinfo.value.code, ErrorCode.GUARDRAIL_BLOCKED)
        payload = json.dumps(excinfo.value.model_dump())
        self.assertNotIn(_FAKE_OPENAI_KEY, payload)

    def test_credential_in_notification_body_is_blocked(self) -> None:
        replies = AssistantReplies.from_catalog(
            {
                "reminder_created_with_notice": "Listo. Te recordaré {title} {minutes_label} antes.",
                "minutes_plural": "{minutes} minutos",
                "reminder_notification_body": "Recordatorio: " + _FAKE_GITHUB_PAT,
            }
        )

        with pytest.raises(GuardrailViolation):
            self.workflow(replies).run(self.principal, self.request())


class RuntimeOutputWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )

    def test_safe_runtime_reply_passes_output_scan(self) -> None:
        result = LocalAgentRuntime().run(
            "organiza mi día",
            principal=self.principal,
            budget=TokenBudget(limit=100),
        )

        self.assertEqual(result.status, AgentStatus.completed)
        self.assertEqual(result.reply, "Solicitud recibida.")

    def test_unsafe_runtime_reply_is_blocked_before_returning(self) -> None:
        runtime = LocalAgentRuntime(
            replies=AssistantReplies.from_catalog(
                {
                    "runtime_request_received": "Recibido. Tu token: "
                    + _FAKE_GITHUB_PAT,
                }
            )
        )

        with pytest.raises(GuardrailViolation) as excinfo:
            runtime.run(
                "organiza mi día",
                principal=self.principal,
                budget=TokenBudget(limit=100),
            )

        self.assertEqual(excinfo.value.code, ErrorCode.GUARDRAIL_BLOCKED)
        payload = json.dumps(excinfo.value.model_dump())
        self.assertNotIn(_FAKE_GITHUB_PAT, payload)


if __name__ == "__main__":
    unittest.main()
