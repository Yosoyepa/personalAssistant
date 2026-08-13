"""Outbox reconciliation HTTP endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI

from personal_assistant.application.dto.delivery import DeliveryStatus
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.http_auth import current_principal
from personal_assistant.infrastructure.http_models import (
    OutboxResolveRequest,
    OutboxResolveResponse,
)


def register_outbox_routes(app: FastAPI, container: AppContainer) -> None:
    """Register endpoints for resolving uncertain outbox deliveries."""

    @app.post(
        "/v1/runtime/outbox/{message_id}/resolve",
        response_model=OutboxResolveResponse,
        tags=["runtime"],
    )
    def resolve_outbox_message(
        message_id: str,
        request: OutboxResolveRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> OutboxResolveResponse:
        approval = ApprovalGrant.issue(
            principal=principal,
            action="notification.resolve_uncertain",
            resource=f"{message_id}:{request.resolution}",
            tier=PermissionTier.P5,
            approval_id=f"runtime-{message_id}-{request.resolution}",
        )
        try:
            resolved = container.reminder_notifications.resolve_uncertain(
                principal=principal,
                message_id=message_id,
                resolution=request.resolution,
                now=datetime.now(UTC),
                approval=approval,
            )
        except ValueError as exc:
            raise AssistantError(
                ErrorCode.VALIDATION_FAILED,
                str(exc),
                tenant_id=principal.tenant_id,
            ) from exc
        status_str = (
            resolved.dispatch_status.value
            if isinstance(resolved.dispatch_status, DeliveryStatus)
            else str(resolved.dispatch_status)
        )
        return OutboxResolveResponse(
            message_id=resolved.id,
            status=status_str,
            attempts=resolved.attempts,
        )
