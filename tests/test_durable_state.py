from __future__ import annotations

import unittest

from personal_assistant.application.dto.workflows import WorkflowState, WorkflowStatus
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.identity import Principal
from personal_assistant.adapters.persistence.in_memory import InMemoryWorkflowStateStore


class DurableStateTests(unittest.TestCase):
    def test_terminal_workflow_state_is_immutable(self) -> None:
        principal = Principal.for_test(principal_id="user-1", tenant_id="tenant-a", permission_tier=PermissionTier.P2)
        store = InMemoryWorkflowStateStore()
        completed = WorkflowState(
            tenant_id="tenant-a",
            workflow_type="reminder.create",
            status=WorkflowStatus.completed,
            step="completed",
            idempotency_key="same",
            data={"result": "done"},
        )
        regressed = completed.model_copy(update={"status": WorkflowStatus.running, "step": "retry"})
        store.upsert(principal, completed)

        with self.assertRaises(AssistantError) as ctx:
            store.upsert(principal, regressed)

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)


if __name__ == "__main__":
    unittest.main()

