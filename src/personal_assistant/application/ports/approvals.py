"""Approval application port."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.application.dto.commands import PendingApproval
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class ApprovalStorePort(Protocol):
    def create(self, principal: Principal, request: PendingApproval) -> PendingApproval:
        """Persist or reuse one pending approval request for the principal."""

    def get(self, principal: Principal, approval_id: str) -> PendingApproval | None:
        """Return one approval request visible to the principal."""

    def list_pending(self, principal: Principal) -> list[PendingApproval]:
        """List approval requests awaiting the principal."""

    def list_for_tenant(self, principal: Principal) -> list[PendingApproval]:
        """List approval requests visible to the principal."""

    def approve(self, principal: Principal, approval_id: str) -> ApprovalGrant:
        """Approve one pending request and issue a trusted grant."""

    def reject(self, principal: Principal, approval_id: str) -> PendingApproval:
        """Reject one pending request."""
