"""FastAPI composition root for the local assistant runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_assistant.adapters.inbound.auth import principal_from_auth_claims
from personal_assistant.adapters.inbound.api import normalize_telegram_webhook
from personal_assistant.adapters.outbound.notifications.telegram import TelegramBotApiClient, TelegramNotificationTool
from personal_assistant.application.dto.channels import NormalizedMessage
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import ReminderWorkflowInput, ReminderWorkflowResult
from personal_assistant.application.dto.runtime import AgentStatus, ApprovalStatus, AudioTranscriptionRequest
from personal_assistant.application.dto.tracing import TraceEvent
from personal_assistant.application.dto.workflows import WorkflowState
from personal_assistant.application.ports.notifications import NotificationRequest
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode, error_response
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.admin import AdminDashboard, clamp_limit, is_local_client, local_admin_principal
from personal_assistant.infrastructure.bootstrap import (
    AppContainer,
    build_container,
    build_llm_provider,
    build_transcription_provider,
)
from personal_assistant.infrastructure.config import AppSettings


MAX_TELEGRAM_AUDIO_BYTES = 20 * 1024 * 1024


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    service: str


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"]
    checks: dict[str, Literal["ok"]]


class ReminderCommandRequest(BaseModel):
    """HTTP transport request for the reminder workflow.

    Tenant and principal fields are deliberately absent; they come from the
    authenticated HTTP boundary and are converted into a trusted Principal.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: Literal["telegram", "whatsapp"] = "telegram"
    recipient: str = Field(min_length=1)
    now: datetime
    timezone: str = "America/Bogota"
    idempotency_key: str | None = None

    def to_workflow_input(self, *, approval: ApprovalGrant | None = None) -> ReminderWorkflowInput:
        return ReminderWorkflowInput(
            message_id=self.message_id,
            conversation_id=self.conversation_id,
            text=self.text,
            channel=self.channel,
            recipient=self.recipient,
            now=self.now,
            timezone=self.timezone,
            idempotency_key=self.idempotency_key,
            approval=approval,
        )


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=500)


class ApprovalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    action: str
    resource: str
    permission_tier: PermissionTier
    reason: str
    status: ApprovalStatus
    created_at: datetime


class ReminderCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    tenant_id_source: Literal["principal"] = "principal"
    status: AgentStatus
    intent: str
    reply: str
    approval_required: bool = False
    approval: ApprovalView | None = None
    calendar_event_id: str | None = None
    reminder_id: str | None = None
    reused: bool = False
    trace_ids: list[str] = Field(default_factory=list)


class ApprovalDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    status: ApprovalStatus
    result: ReminderCommandResponse | None = None


class TelegramWebhookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    status: AgentStatus
    reply: str
    sent: bool = False
    approval_id: str | None = None
    command: str | None = None


@dataclass(slots=True)
class PendingReminderApproval:
    approval_id: str
    principal_id: str
    tenant_id: str
    request: ReminderCommandRequest
    idempotency_key: str
    action: str
    resource: str
    permission_tier: PermissionTier
    reason: str
    status: ApprovalStatus = ApprovalStatus.pending
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def view(self) -> ApprovalView:
        return ApprovalView(
            approval_id=self.approval_id,
            action=self.action,
            resource=self.resource,
            permission_tier=self.permission_tier,
            reason=self.reason,
            status=self.status,
            created_at=self.created_at,
        )


def _status_for_error(code: ErrorCode) -> int:
    return {
        ErrorCode.AUTHENTICATION_REQUIRED: 401,
        ErrorCode.TENANT_REQUIRED: 400,
        ErrorCode.PERMISSION_DENIED: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.CONFLICT: 409,
        ErrorCode.TOKEN_BUDGET_EXCEEDED: 429,
        ErrorCode.VALIDATION_FAILED: 422,
        ErrorCode.GUARDRAIL_BLOCKED: 422,
        ErrorCode.PII_DETECTED: 422,
        ErrorCode.PROMPT_INJECTION_DETECTED: 422,
    }.get(code, 500)


def _effective_idempotency_key(principal: Principal, request: ReminderCommandRequest) -> str:
    return request.idempotency_key or reminder_idempotency_key(principal.tenant_id, request.message_id, request.text)


def _approval_id(tenant_id: str, idempotency_key: str, action: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{idempotency_key}:{action}".encode("utf-8")).hexdigest()[:24]
    return f"apr_{digest}"


def _reminder_response(
    *,
    principal: Principal,
    run_id: str,
    result: ReminderWorkflowResult,
    approval: ApprovalView | None = None,
) -> ReminderCommandResponse:
    return ReminderCommandResponse(
        run_id=run_id,
        tenant_id=principal.tenant_id,
        status=result.status,
        intent=result.intent.value,
        reply=result.reply,
        approval_required=result.approval_required,
        approval=approval,
        calendar_event_id=result.calendar_event_id,
        reminder_id=result.reminder_id,
        reused=result.reused,
        trace_ids=result.trace_ids,
    )


def _assert_same_actor(pending: PendingReminderApproval, principal: Principal) -> None:
    if pending.tenant_id != principal.tenant_id or pending.principal_id != principal.principal_id:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "approval belongs to a different principal or tenant",
            tenant_id=principal.tenant_id,
        )


def build_runtime_container(settings: AppSettings) -> AppContainer:
    llm = build_llm_provider(settings)
    transcription = build_transcription_provider(settings)
    if settings.telegram_bot_token:
        telegram_notifications = TelegramNotificationTool(
            TelegramBotApiClient(token=settings.telegram_bot_token),
        )
        return build_container(
            llm=llm,
            notifications=telegram_notifications,
            transcription=transcription,
            approve_reminder_notifications=True,
        )
    return build_container(llm=llm, transcription=transcription)


def current_principal(
    x_principal_id: Annotated[str | None, Header(alias="X-Principal-Id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_permission_tier: Annotated[str, Header(alias="X-Permission-Tier")] = PermissionTier.P0.value,
    x_scopes: Annotated[str | None, Header(alias="X-Scopes")] = None,
) -> Principal:
    if not x_principal_id:
        raise AssistantError(ErrorCode.AUTHENTICATION_REQUIRED, "X-Principal-Id header is required")
    if not x_tenant_id:
        raise AssistantError(ErrorCode.TENANT_REQUIRED, "X-Tenant-Id header is required")
    try:
        tier = PermissionTier(x_permission_tier)
    except ValueError as exc:
        raise AssistantError(
            ErrorCode.VALIDATION_FAILED,
            "X-Permission-Tier must be one of P0-P6",
            field="X-Permission-Tier",
        ) from exc
    return principal_from_auth_claims(
        {"sub": x_principal_id, "tenant_id": x_tenant_id, "scope": x_scopes or ""},
        auth_provider="local-http",
        permission_tier=tier,
    )


def telegram_principal(settings: AppSettings, actor_id: str) -> Principal:
    if settings.telegram_allowed_user_ids and actor_id not in settings.telegram_allowed_user_ids:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "telegram user is not allowed",
            tenant_id=settings.tenant_id,
        )
    return principal_from_auth_claims(
        {"sub": actor_id, "tenant_id": settings.tenant_id},
        auth_provider="telegram",
        permission_tier=PermissionTier.P5,
    )


def _send_telegram_reply(
    container: AppContainer,
    principal: Principal,
    *,
    chat_id: str,
    text: str,
    idempotency_key: str,
) -> bool:
    request = NotificationRequest(
        channel="telegram",
        recipient=chat_id,
        body=text,
        idempotency_key=f"{idempotency_key}:reply",
    )
    approval = ApprovalGrant.issue(
        principal=principal,
        action="notification.send",
        resource=request.idempotency_key,
        tier=PermissionTier.P5,
        approval_id=f"{idempotency_key}:reply",
    )
    try:
        container.notifications.send(principal, request, approval=approval)
    except Exception:
        # Telegram already delivered the update; provider send failures should
        # not force Telegram to retry the webhook and duplicate workflow work.
        return False
    return True


def _transcribe_telegram_media(
    container: AppContainer,
    settings: AppSettings,
    message: NormalizedMessage,
) -> tuple[NormalizedMessage | None, str | None]:
    if not message.media_file_id:
        return None, "Recibí un audio, pero Telegram no envió un file_id utilizable."
    if container.transcription is None:
        return None, (
            "Recibí tu audio, pero falta configurar transcripción. "
            "Activa TRANSCRIPTION_PROVIDER y TRANSCRIPTION_API_KEY en el backend."
        )
    if not settings.telegram_bot_token:
        return None, "Recibí tu audio, pero falta TELEGRAM_BOT_TOKEN para descargarlo desde Telegram."
    if message.media_file_size is not None and message.media_file_size > MAX_TELEGRAM_AUDIO_BYTES:
        return None, "El audio supera el límite local de 20MB."

    client = TelegramBotApiClient(token=settings.telegram_bot_token)
    file_info = client.get_file(file_id=message.media_file_id)
    file_path = str(file_info.get("file_path") or "")
    if not file_path:
        return None, "No pude resolver el archivo de audio en Telegram."
    audio = client.download_file(file_path=file_path)
    if len(audio) > MAX_TELEGRAM_AUDIO_BYTES:
        return None, "El audio descargado supera el límite local de 20MB."

    extension = file_path.rsplit(".", 1)[-1] if "." in file_path else "ogg"
    transcript = container.transcription.transcribe(
        AudioTranscriptionRequest(
            filename=f"telegram-{message.message_id}.{extension}",
            content_type=message.media_mime_type or "audio/ogg",
            data=audio,
            language="es",
            prompt="Transcribe mensajes de voz en español para crear recordatorios o citas.",
        ),
        budget=TokenBudget(limit=4_000),
    )
    return (
        message.model_copy(
            update={
                "text": transcript.text,
                "command": None,
                "command_args": "",
            }
        ),
        None,
    )


def _admin_principal(settings: AppSettings, tenant_id: str | None, principal_id: str) -> Principal:
    return local_admin_principal(
        tenant_id=tenant_id or settings.tenant_id,
        principal_id=principal_id,
        permission_tier=PermissionTier.P0,
    )


def _require_local_admin(request: Request) -> None:
    client_host = request.client.host if request.client is not None else None
    if not is_local_client(client_host):
        raise AssistantError(ErrorCode.PERMISSION_DENIED, "admin API is local-only")


def create_app(container: AppContainer | None = None, settings: AppSettings | None = None) -> FastAPI:
    runtime_settings = settings or AppSettings.from_env()
    runtime_container = container or build_runtime_container(runtime_settings)
    pending_approvals: dict[str, PendingReminderApproval] = {}
    app = FastAPI(title="Personal Assistant Runtime", version="0.1.0")
    app.state.container = runtime_container
    app.state.pending_approvals = pending_approvals
    app.state.settings = runtime_settings
    dashboard = AdminDashboard(runtime_container)

    @app.exception_handler(AssistantError)
    async def handle_assistant_error(_: Any, exc: AssistantError) -> JSONResponse:
        return JSONResponse(status_code=_status_for_error(exc.code), content=jsonable_encoder(exc.model_dump()))

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(_: Any, exc: RequestValidationError) -> JSONResponse:
        response = error_response(
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            context={"errors": exc.errors()},
        )
        return JSONResponse(status_code=422, content=jsonable_encoder(response.model_dump(mode="json")))

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Any, exc: ValidationError) -> JSONResponse:
        response = error_response(
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            context={"errors": exc.errors()},
        )
        return JSONResponse(status_code=422, content=jsonable_encoder(response.model_dump(mode="json")))

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Any, exc: ValueError) -> JSONResponse:
        response = error_response(ErrorCode.VALIDATION_FAILED, str(exc))
        return JSONResponse(status_code=422, content=jsonable_encoder(response.model_dump(mode="json")))

    @app.get("/healthz", response_model=HealthResponse, tags=["runtime"])
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", service="personal_assistant")

    @app.get("/readyz", response_model=ReadinessResponse, tags=["runtime"])
    def readyz() -> ReadinessResponse:
        return ReadinessResponse(
            status="ready",
            checks={
                "container": "ok",
                "calendar": "ok",
                "scheduler": "ok",
                "state_store": "ok",
                "trace_recorder": "ok",
            },
        )

    @app.post(
        "/webhooks/telegram/{secret}",
        response_model=TelegramWebhookResponse,
        tags=["telegram"],
    )
    def telegram_webhook(
        secret: str,
        payload: dict[str, Any],
        x_telegram_secret: Annotated[
            str | None,
            Header(alias="X-Telegram-Bot-Api-Secret-Token"),
        ] = None,
    ) -> TelegramWebhookResponse:
        if secret != runtime_settings.telegram_webhook_secret:
            raise AssistantError(ErrorCode.PERMISSION_DENIED, "invalid telegram webhook secret")
        if x_telegram_secret is not None and x_telegram_secret != runtime_settings.telegram_webhook_secret:
            raise AssistantError(ErrorCode.PERMISSION_DENIED, "invalid telegram secret token")

        message = normalize_telegram_webhook(payload, tenant_id=runtime_settings.tenant_id)
        principal = telegram_principal(runtime_settings, message.actor_id)
        if message.media_kind in {"voice", "audio"}:
            transcribed, transcription_error = _transcribe_telegram_media(runtime_container, runtime_settings, message)
            if transcription_error is not None:
                sent = False
                if runtime_settings.telegram_bot_token:
                    sent = _send_telegram_reply(
                        runtime_container,
                        principal,
                        chat_id=message.conversation_id,
                        text=transcription_error,
                        idempotency_key=message.idempotency_key
                        or f"telegram:{message.conversation_id}:{message.message_id}",
                    )
                return TelegramWebhookResponse(
                    status=AgentStatus.needs_clarification,
                    reply=transcription_error,
                    sent=sent,
                    approval_id=None,
                    command=message.command,
                )
            if transcribed is not None:
                message = transcribed

        result = runtime_container.commands.handle(
            principal,
            message,
            now=datetime.now(UTC),
            timezone=runtime_settings.timezone,
        )
        sent = False
        if runtime_settings.telegram_bot_token:
            sent = _send_telegram_reply(
                runtime_container,
                principal,
                chat_id=message.conversation_id,
                text=result.reply,
                idempotency_key=message.idempotency_key or f"telegram:{message.conversation_id}:{message.message_id}",
            )
        return TelegramWebhookResponse(
            status=result.status,
            reply=result.reply,
            sent=sent,
            approval_id=result.approval_id,
            command=message.command,
        )

    @app.get("/admin", response_class=HTMLResponse, tags=["admin"])
    def admin_page(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> HTMLResponse:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return HTMLResponse(dashboard.render_html(principal, limit=clamp_limit(limit)))

    @app.get("/admin/snapshot", tags=["admin"])
    def admin_snapshot(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.snapshot(principal, limit=clamp_limit(limit))

    @app.get("/admin/health", tags=["admin"])
    def admin_health(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.snapshot(principal, limit=clamp_limit(limit))["health"]

    @app.get("/admin/approvals", tags=["admin"])
    def admin_approvals(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.approvals(principal, limit=clamp_limit(limit))

    @app.get("/admin/traces", tags=["admin"])
    def admin_traces(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.traces(principal, limit=clamp_limit(limit))

    @app.get("/admin/outbox", tags=["admin"])
    def admin_outbox(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.outbox(principal, limit=clamp_limit(limit))

    @app.get("/admin/scheduler", tags=["admin"])
    def admin_scheduler(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.scheduler(principal, limit=clamp_limit(limit))

    @app.get("/admin/events", tags=["admin"])
    def admin_events(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.events(principal, limit=clamp_limit(limit))

    @app.get("/admin/states", tags=["admin"])
    def admin_states(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.states(principal, limit=clamp_limit(limit))

    @app.get("/admin/memory", tags=["admin"])
    def admin_memory(
        request: Request,
        tenant_id: Annotated[str | None, Query(min_length=1)] = None,
        principal_id: Annotated[str, Query(min_length=1)] = "local-admin",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        _require_local_admin(request)
        principal = _admin_principal(runtime_settings, tenant_id, principal_id)
        return dashboard.memory(principal, limit=clamp_limit(limit))

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
        result = runtime_container.reminder_workflow.run(principal, request.to_workflow_input())
        approval_view: ApprovalView | None = None
        if result.approval_required:
            response.status_code = 202
            action = "calendar.create_event"
            approval_id = _approval_id(principal.tenant_id, run_id, action)
            pending = pending_approvals.get(approval_id)
            if pending is None:
                pending = PendingReminderApproval(
                    approval_id=approval_id,
                    principal_id=principal.principal_id,
                    tenant_id=principal.tenant_id,
                    request=request,
                    idempotency_key=run_id,
                    action=action,
                    resource=f"{run_id}:calendar",
                    permission_tier=PermissionTier.P3,
                    reason="Crear evento externo de calendario para el recordatorio.",
                )
                pending_approvals[approval_id] = pending
            approval_view = pending.view()
        return _reminder_response(principal=principal, run_id=run_id, result=result, approval=approval_view)

    @app.get(
        "/v1/runtime/approvals",
        response_model=list[ApprovalView],
        tags=["runtime"],
    )
    def list_approvals(
        principal: Annotated[Principal, Depends(current_principal)],
        status: Annotated[ApprovalStatus | None, Query()] = None,
    ) -> list[ApprovalView]:
        approvals = [
            pending.view()
            for pending in pending_approvals.values()
            if pending.tenant_id == principal.tenant_id and pending.principal_id == principal.principal_id
        ]
        if status is not None:
            approvals = [approval for approval in approvals if approval.status == status]
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
        pending = pending_approvals.get(approval_id)
        if pending is None:
            raise AssistantError(ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id)
        _assert_same_actor(pending, principal)
        if pending.status == ApprovalStatus.rejected:
            raise AssistantError(ErrorCode.CONFLICT, "approval was already rejected", tenant_id=principal.tenant_id)

        grant = ApprovalGrant.issue(
            principal=principal,
            action=pending.action,
            resource=pending.resource,
            tier=pending.permission_tier,
            approval_id=pending.approval_id,
        )
        result = runtime_container.reminder_workflow.run(principal, pending.request.to_workflow_input(approval=grant))
        pending.status = ApprovalStatus.approved
        return ApprovalDecisionResponse(
            approval_id=approval_id,
            status=pending.status,
            result=_reminder_response(principal=principal, run_id=pending.idempotency_key, result=result),
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
        pending = pending_approvals.get(approval_id)
        if pending is None:
            raise AssistantError(ErrorCode.NOT_FOUND, "approval not found", tenant_id=principal.tenant_id)
        _assert_same_actor(pending, principal)
        if pending.status == ApprovalStatus.approved:
            raise AssistantError(ErrorCode.CONFLICT, "approval was already approved", tenant_id=principal.tenant_id)
        pending.status = ApprovalStatus.rejected
        return ApprovalDecisionResponse(approval_id=approval_id, status=pending.status)

    @app.get(
        "/v1/runtime/workflows",
        response_model=list[WorkflowState],
        tags=["runtime"],
    )
    def list_workflows(principal: Annotated[Principal, Depends(current_principal)]) -> list[WorkflowState]:
        return runtime_container.states.list_for_tenant(principal)

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
            return runtime_container.traces.list_for_run(principal.tenant_id, run_id)
        return runtime_container.traces.list_for_tenant(principal.tenant_id)

    return app


app = create_app()
