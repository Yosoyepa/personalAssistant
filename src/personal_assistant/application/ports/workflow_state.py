"""Workflow state application port."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.application.dto.workflows import (
    WorkflowState,
    WorkflowStateRegistration,
)
from personal_assistant.domain.common.identity import Principal


class WorkflowStateStorePort(Protocol):
    def register_or_replay(
        self,
        principal: Principal,
        state: WorkflowState,
        *,
        resume_from_step: str | None = None,
    ) -> WorkflowStateRegistration:
        """Atomically register a workflow or return its matching replay state.

        Implementations must reject the same identity/key with a different
        payload fingerprint before any workflow side effect can execute. When
        ``resume_from_step`` matches a waiting state, exactly one caller must
        atomically acquire the resumed running execution.
        """

    def upsert(self, principal: Principal, state: WorkflowState) -> WorkflowState:
        """Persist lifecycle progress without changing identity/fingerprint."""

    def get_by_idempotency_key(
        self, principal: Principal, idempotency_key: str
    ) -> WorkflowState | None:
        """Read workflow state by idempotency key for the authenticated tenant."""

    def list_for_tenant(self, principal: Principal) -> list[WorkflowState]:
        """List workflow state records visible to the authenticated tenant."""
