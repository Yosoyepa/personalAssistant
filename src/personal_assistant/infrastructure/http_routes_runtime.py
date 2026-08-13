"""Runtime endpoints for reminders, approvals, workflows, and traces."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Query, Response

from personal_assistant.application.dto.commands import PendingApprovalStatus
from personal_assistant.application.dto.runtime import ApprovalStatus
from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.application.dto.workflows import WorkflowState
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.infrastructure.bootstrap import AppContainer
from personal_assistant.infrastructure.http_auth import current_principal
from personal_assistant.infrastructure.http_converters import (
    _approval_status_from_pending,
    _approval_view_from_pending,
    _effective_idempotency_key,
    _pending_approval_from_request,
    _pending_status_from_approval,
    _reminder_response,
    _workflow_input_from_pending,
)
from personal_assistant.infrastructure.http_models import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalView,
    ReminderCommandRequest,
    ReminderCommandResponse,
)


def register_runtime_routes(
    app: FastAPI,
    container: AppContainer,
) -> None:
    """Register core runtime execution, approval, workflow, and trace routes."""
    replies = container.commands.replies

    @app.post(
        "/v1/runtime/reminders",
        response_model=ReminderCommandResponse,
        tags=["runtime"],
    )
    def create_reminder(
        request: ReminderCommandRequest,
        response: Response,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ReminderCommandResponse:
        run_id = _effective_idempotency_key(principal, request)
        result = container.reminder_workflow.run(principal, request.to_workflow_input())
        approval_view: ApprovalView | None = None
        if result.approval_required:
            response.status_code = 202
            action = "calendar.create_event"
            pending = container.approvals.create(
                principal,
                _pending_approval_from_request(
                    principal=principal,
                    request=request,
                    run_id=run_id,
                    payload_fingerprint=result.payload_fingerprint,
                    action=action,
                ),
            )
            approval_view = _approval_view_from_pending(
                pending,
                reason=replies.approval_reason_calendar_create_event(),
            )
        return _reminder_response(
            principal=principal, run_id=run_id, result=result, approval=approval_view
        )

    @app.get(
        "/v1/runtime/approvals",
        response_model=list[ApprovalView],
        tags=["runtime"],
    )
    def list_approvals(
        principal: Annotated[Principal, Depends(current_principal)],
        status: Annotated[ApprovalStatus | None, Query()] = None,
    ) -> list[ApprovalView]:
        pending_approvals = container.approvals.list_for_tenant(principal)
        if status is not None:
            expected = _pending_status_from_approval(status)
            if expected is None:
                return []
            pending_approvals = [
                approval
                for approval in pending_approvals
                if approval.status == expected
            ]
        approvals = [
            _approval_view_from_pending(
                approval,
                reason=replies.approval_reason_calendar_create_event(),
            )
            for approval in pending_approvals
        ]
        return sorted(approvals, key=lambda approval: approval.created_at)

    @app.post(
        "/v1/runtime/approvals/{approval_id}/approve",
        response_model=ApprovalDecisionResponse,
        tags=["runtime"],
    )
    def approve(
        approval_id: str,
        principal: Annotated[Principal, Depends(current_principal)],
        _: ApprovalDecisionRequest | None = None,
    ) -> ApprovalDecisionResponse:
        pending = container.approvals.get(principal, approval_id)
        if pending is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if pending.status == PendingApprovalStatus.cancelled:
            raise AssistantError(
                ErrorCode.CONFLICT,
                "approval was already rejected",
                tenant_id=principal.tenant_id,
            )

        grant = container.approvals.approve(principal, approval_id)
        result = container.reminder_workflow.run(
            principal,
            _workflow_input_from_pending(pending, approval=grant),
        )
        return ApprovalDecisionResponse(
            approval_id=approval_id,
            status=ApprovalStatus.approved,
            result=_reminder_response(
                principal=principal, run_id=pending.idempotency_key, result=result
            ),
        )

    @app.post(
        "/v1/runtime/approvals/{approval_id}/reject",
        response_model=ApprovalDecisionResponse,
        tags=["runtime"],
    )
    def reject(
        approval_id: str,
        principal: Annotated[Principal, Depends(current_principal)],
        _: ApprovalDecisionRequest | None = None,
    ) -> ApprovalDecisionResponse:
        existing = container.approvals.get(principal, approval_id)
        if existing is None:
            raise AssistantError(
                ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id
            )
        if existing.status == PendingApprovalStatus.approved:
            raise AssistantError(
                ErrorCode.CONFLICT,
                "approval was already approved",
                tenant_id=principal.tenant_id,
            )
        pending = container.approvals.reject(principal, approval_id)
        return ApprovalDecisionResponse(
            approval_id=approval_id,
            status=_approval_status_from_pending(pending.status),
        )

    @app.get(
        "/v1/runtime/workflows",
        response_model=list[WorkflowState],
        tags=["runtime"],
    )
    def list_workflows(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[WorkflowState]:
        return container.states.list_for_tenant(principal)

    @app.get(
        "/v1/runtime/traces",
        response_model=list[TraceEvent],
        tags=["runtime"],
    )
    def list_traces(
        principal: Annotated[Principal, Depends(current_principal)],
        run_id: Annotated[str | None, Query(min_length=1)] = None,
    ) -> list[TraceEvent]:
        if run_id is not None:
            return container.traces.list_for_run(principal, run_id)
        return container.traces.list_for_tenant(principal)
