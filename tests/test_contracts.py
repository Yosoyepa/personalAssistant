from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from personal_assistant.agent_registry.a2a import AgentTask, Message, MessageRole, personal_assistant_card
from personal_assistant.agent_runtime.models import ToolCall
from personal_assistant.shared.permissions import PermissionTier
from personal_assistant.tools.contracts import CALENDAR_CREATE_CONTRACT, NOTIFICATION_SEND_CONTRACT, SideEffect


class ContractTests(unittest.TestCase):
    def test_a2a_card_and_task_are_serializable(self) -> None:
        card = personal_assistant_card()
        payload = card.to_json()
        restored = type(card).model_validate_json(payload)

        self.assertEqual(restored.agent_id, "personal_assistant")
        self.assertEqual(restored.skills[0].id, "reminder.create")

        task = AgentTask(
            agent_id="personal_assistant",
            tenant_id="tenant-a",
            messages=[
                Message(
                    role=MessageRole.user,
                    tenant_id="tenant-a",
                    parts=[{"type": "text", "text": "recuerdame clase el martes a las 5"}],
                )
            ],
        )
        decoded = json.loads(task.to_json())
        self.assertEqual(decoded["tenant_id"], "tenant-a")
        self.assertEqual(decoded["messages"][0]["role"], "user")
        restored_task = AgentTask.model_validate_json(task.to_json())
        self.assertEqual(restored_task.to_dict(), task.to_dict())

    def test_tool_contracts_encode_approval_and_idempotency(self) -> None:
        self.assertEqual(CALENDAR_CREATE_CONTRACT.permission_tier, PermissionTier.P3)
        self.assertEqual(CALENDAR_CREATE_CONTRACT.side_effect, SideEffect.external_write)
        self.assertTrue(CALENDAR_CREATE_CONTRACT.approval_required)
        self.assertTrue(CALENDAR_CREATE_CONTRACT.idempotency_required)
        self.assertIn("idempotency_key", CALENDAR_CREATE_CONTRACT.audit_requirements)
        self.assertIn("tenant_id must come from Principal", CALENDAR_CREATE_CONTRACT.tenant_isolation)

        self.assertEqual(NOTIFICATION_SEND_CONTRACT.permission_tier, PermissionTier.P5)
        self.assertEqual(NOTIFICATION_SEND_CONTRACT.side_effect, SideEffect.communication)
        self.assertTrue(NOTIFICATION_SEND_CONTRACT.approval_required)
        self.assertTrue(NOTIFICATION_SEND_CONTRACT.idempotency_required)

    def test_mcp_and_a2a_tool_calls_fail_closed_in_mvp(self) -> None:
        for tool_name in ("mcp.search", "a2a.delegate"):
            with self.subTest(tool_name=tool_name):
                with self.assertRaises(ValidationError):
                    ToolCall(name=tool_name)


if __name__ == "__main__":
    unittest.main()
