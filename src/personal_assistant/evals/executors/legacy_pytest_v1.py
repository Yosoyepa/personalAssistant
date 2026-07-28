"""Migration adapter that executes an allowlisted repository test node."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pydantic import Field, field_validator

from personal_assistant.evals.schema import StrictModel


MIGRATED_TEST_NODES = frozenset(
    {
        "tests/test_admin_dashboard.py::AdminDashboardTests::test_snapshot_is_tenant_and_actor_scoped",
        "tests/test_command_router.py::CommandRouterTests::test_reminder_command_creates_pending_approval_without_side_effect",
        "tests/test_contracts.py::ContractTests::test_a2a_card_and_task_are_serializable",
        "tests/test_contracts.py::ContractTests::test_tool_contracts_encode_approval_and_idempotency",
        "tests/test_documents_and_channels.py::DocumentAndChannelTests::test_document_prompt_injection_is_warned_not_executed",
        "tests/test_documents_and_channels.py::DocumentAndChannelTests::test_telegram_normalizer_requires_external_tenant",
        "tests/test_http_runtime.py::HttpRuntimeTests::test_approval_resumes_workflow_and_reuses_completed_state",
        "tests/test_http_runtime.py::HttpRuntimeTests::test_body_cannot_supply_tenant_authority",
        "tests/test_permissions_and_tenant.py::PermissionAndTenantTests::test_calendar_events_are_tenant_scoped",
        "tests/test_permissions_and_tenant.py::PermissionAndTenantTests::test_memory_retrieval_is_tenant_scoped",
        "tests/test_reminder_boundary_commands.py::test_temporal_ambiguity_stops_before_approval_and_effects",
        "tests/test_reminder_workflow.py::ReminderWorkflowTests::test_duplicate_webhook_reuses_completed_state",
        "tests/test_reminder_workflow.py::ReminderWorkflowTests::test_missing_approval_does_not_create_side_effect",
        "tests/test_reminder_workflow.py::ReminderWorkflowTests::test_waiting_approval_replay_with_legitimate_grant_resumes",
        "tests/test_telegram_notifications.py::TelegramNotificationTests::test_telegram_send_requires_p5_approval_before_dispatch_or_replay",
    }
)


class InputModel(StrictModel):
    testNode: str = Field(min_length=1)

    @field_validator("testNode")
    @classmethod
    def repository_test_only(cls, value: str) -> str:
        if value not in MIGRATED_TEST_NODES:
            raise ValueError("testNode is not part of the immutable legacy migration")
        return value


class ExpectedModel(StrictModel):
    passed: bool


def _subprocess_env() -> dict[str, str]:
    """Build the minimum Windows process environment without inherited secrets."""

    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def execute(value: InputModel) -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", value.testNode],
        cwd=repository_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_subprocess_env(),
        timeout=60,
        check=False,
    )
    return {"passed": completed.returncode == 0}
