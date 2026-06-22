"""Approval application port."""

from __future__ import annotations

from typing import Protocol

from personal_assistant.application.dto.runtime import ApprovalRequest
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant


class ApprovalStorePort(Protocol):
    def create(self, principal: Principal, request: ApprovalRequest) -> ApprovalRequest:
        """Persist or reuse one pending approval request for the principal."""

    def get(self, principal: Principal, approval_id: str) -> ApprovalRequest | None:
        """Return one approval request visible to the principal."""

    def list_pending(self, principal: Principal) -> list[ApprovalRequest]:
        """List non-expired approval requests awaiting the principal."""

    def approve(self, principal: Principal, approval_id: str) -> ApprovalGrant:
        """Approve one pending request and issue a trusted grant."""

    def reject(self, principal: Principal, approval_id: str) -> ApprovalRequest:
        """Reject one pending request."""
