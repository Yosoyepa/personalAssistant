"""Workflow state application port."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.domain.common.durable import WorkflowState
from personal_assistant.domain.common.identity import Principal


class WorkflowStateStorePort(Protocol):
    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        """Persist workflow state for the authenticated tenant."""

    def get_by_idempotency_key(self, principal: Principal, idempotency_key: str) -> WorkflowState | None:
        """Read workflow state by idempotency key for the authenticated tenant."""
