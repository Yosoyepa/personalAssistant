"""FastAPI composition root for the local assistant runtime."""

from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, Query, Request, Response, Security
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_assistant.adapters.inbound.auth import (
    LocalPrincipalProvider,
    principal_from_auth_claims,
)
from personal_assistant.adapters.inbound.api import normalize_telegram_webhook
from personal_assistant.adapters.inbound.channels.telegram import (
    TelegramActorNotVerifiableError,
)
from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
    TelegramNotificationTool,
)
from personal_assistant.application.dto.channels import NormalizedMessage
from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
)
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import (
    ReminderWorkflowInput,
    ReminderWorkflowResult,
)
from personal_assistant.application.dto.runtime import (
    AgentStatus,
    ApprovalStatus,
    AudioSynthesisRequest,
    AudioTranscriptionRequest,
)
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.application.dto.workflows import WorkflowState
from personal_assistant.application.ports.notifications import (
    NotificationMedia,
    NotificationRequest,
)
from personal_assistant.application.services.replies import AssistantReplies
from personal_assistant.application.use_cases.reminders import reminder_idempotency_key
from personal_assistant.domain.common.exceptions import (
    AssistantError,
    ErrorCode,
    error_response,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.domain.reminders.idempotency import ReminderIdempotencyConflict
from personal_assistant.infrastructure.admin import (
    AdminDashboard,
    clamp_limit,
    local_admin_principal,
)
from personal_assistant.infrastructure.bootstrap import (
    AppContainer,
    build_container,
    build_llm_provider,
    build_transcription_provider,
    build_tts_provider,
)
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.prompts import build_prompt_catalog


MAX_TELEGRAM_AUDIO_BYTES = 20 * 1024 * 1024
TELEGRAM_WEBHOOK_SECRET_HEADER = APIKeyHeader(
    name="X-Telegram-Bot-Api-Secret-Token",
    scheme_name="TelegramWebhookSecret",
    auto_error=False,
)
SUPPORTED_TRANSCRIPTION_EXTENSIONS = frozenset(
    {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "opus", "wav", "webm"}
)


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
    source_event_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: Literal["telegram", "whatsapp"] = "telegram"
    recipient: str = Field(min_length=1)
    now: datetime
    timezone: str = "America/Bogota"
    idempotency_key: str | None = None

    def to_workflow_input(
        self, *, approval: ApprovalGrant | None = None
    ) -> ReminderWorkflowInput:
        return ReminderWorkflowInput(
            message_id=self.message_id,
            source_event_id=self.source_event_id,
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
    source_event_id: str
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    timezone: str
    clarification_reason: str | None = None
    clarification_reply_id: str | None = None
    clarification_reply_version: str | None = None
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
    audio_sent: bool = False
    approval_id: str | None = None
    command: str | None = None


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


def _effective_idempotency_key(
    principal: Principal, request: ReminderCommandRequest
) -> str:
    return reminder_idempotency_key(
        tenant_id=principal.tenant_id,
        channel=request.channel,
        principal_id=principal.principal_id,
        conversation_id=request.conversation_id,
        source_event_id=request.source_event_id,
    )


def _approval_id(
    tenant_id: str, principal_id: str, idempotency_key: str, action: str
) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}:{principal_id}:{idempotency_key}:{action}".encode("utf-8")
    ).hexdigest()[:24]
    return f"apr_{digest}"


def _approval_status_from_pending(status: PendingApprovalStatus) -> ApprovalStatus:
    if status == PendingApprovalStatus.approved:
        return ApprovalStatus.approved
    if status == PendingApprovalStatus.cancelled:
        return ApprovalStatus.rejected
    return ApprovalStatus.pending


def _pending_status_from_approval(
    status: ApprovalStatus,
) -> PendingApprovalStatus | None:
    if status == ApprovalStatus.approved:
        return PendingApprovalStatus.approved
    if status == ApprovalStatus.rejected:
        return PendingApprovalStatus.cancelled
    if status == ApprovalStatus.pending:
        return PendingApprovalStatus.pending
    return None


def _approval_view_from_pending(
    pending: PendingApproval, *, reason: str
) -> ApprovalView:
    return ApprovalView(
        approval_id=pending.approval_id,
        action=pending.action,
        resource=pending.resource,
        permission_tier=PermissionTier(pending.tier),
        reason=reason,
        status=_approval_status_from_pending(pending.status),
        created_at=pending.created_at,
    )


def _pending_approval_from_request(
    *,
    principal: Principal,
    request: ReminderCommandRequest,
    run_id: str,
    payload_fingerprint: str,
    action: str,
) -> PendingApproval:
    return PendingApproval(
        approval_id=_approval_id(
            principal.tenant_id, principal.principal_id, run_id, action
        ),
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        action=action,
        resource=f"{run_id}:calendar",
        tier=PermissionTier.P3.value,
        workflow_kind="reminder.create",
        message_id=request.message_id,
        source_event_id=request.source_event_id,
        conversation_id=request.conversation_id,
        channel=request.channel,
        recipient=request.recipient,
        request_text=request.text,
        request_now=request.now,
        timezone=request.timezone,
        idempotency_key=run_id,
        payload_fingerprint=payload_fingerprint,
    )


def _workflow_input_from_pending(
    pending: PendingApproval,
    *,
    approval: ApprovalGrant,
) -> ReminderWorkflowInput:
    return ReminderWorkflowInput(
        message_id=pending.message_id,
        source_event_id=pending.source_event_id,
        conversation_id=pending.conversation_id,
        text=pending.request_text,
        channel=cast(Literal["telegram", "whatsapp"], pending.channel),
        recipient=pending.recipient,
        now=pending.request_now,
        timezone=pending.timezone,
        idempotency_key=pending.idempotency_key,
        approval=approval,
    )


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
        source_event_id=result.source_event_id,
        payload_fingerprint=result.payload_fingerprint,
        timezone=result.timezone,
        clarification_reason=(
            result.clarification_reason.value
            if result.clarification_reason is not None
            else None
        ),
        clarification_reply_id=result.clarification_reply_id,
        clarification_reply_version=result.clarification_reply_version,
        approval_required=result.approval_required,
        approval=approval,
        calendar_event_id=result.calendar_event_id,
        reminder_id=result.reminder_id,
        reused=result.reused,
        trace_ids=result.trace_ids,
    )


def build_runtime_container(settings: AppSettings) -> AppContainer:
    prompts = build_prompt_catalog()
    llm = build_llm_provider(settings, prompt_catalog=prompts)
    transcription = build_transcription_provider(settings)
    tts = build_tts_provider(settings)
    if settings.telegram_bot_token:
        telegram_notifications = TelegramNotificationTool(
            TelegramBotApiClient(token=settings.telegram_bot_token),
        )
        return build_container(
            settings=settings,
            llm=llm,
            notifications=telegram_notifications,
            transcription=transcription,
            tts=tts,
            prompt_catalog=prompts,
            approve_reminder_notifications=True,
            reminder_minutes_before=settings.reminder_minutes_before,
        )
    return build_container(
        settings=settings,
        llm=llm,
        transcription=transcription,
        tts=tts,
        prompt_catalog=prompts,
        reminder_minutes_before=settings.reminder_minutes_before,
    )


def _run_reminder_worker_loop(
    *,
    container: AppContainer,
    settings: AppSettings,
    stop_event: threading.Event,
) -> None:
    principal = local_admin_principal(
        tenant_id=settings.tenant_id,
        principal_id="reminder-worker",
        permission_tier=PermissionTier.P5,
    )
    while not stop_event.is_set():
        try:
            container.reminder_worker.run_once(principal)
        except Exception as exc:
            container.traces.write(
                TraceEvent(
                    run_id="reminder-worker",
                    agent_id="personal_assistant",
                    event_type=TraceEventType.agent_failed,
                    tenant_id=settings.tenant_id,
                    error={"type": exc.__class__.__name__, "message": str(exc)[:240]},
                )
            )
        stop_event.wait(settings.reminder_worker_interval_seconds)


def current_principal(request: Request) -> Principal:
    """Authenticate one server-owned principal for local HTTP surfaces."""

    provider = cast(
        LocalPrincipalProvider | None,
        getattr(request.app.state, "local_principal_provider", None),
    )
    if provider is None:
        raise AssistantError(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "valid local bearer credentials are required",
        )
    peer_host = request.client.host if request.client is not None else None
    return provider.authenticate(peer_host=peer_host, headers=request.headers)


def telegram_principal(settings: AppSettings, actor_id: str) -> Principal:
    if not actor_id or actor_id not in settings.telegram_allowed_user_ids:
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


def _require_telegram_webhook_secret(
    settings: AppSettings, supplied_secret: str | None
) -> None:
    expected_secret = settings.telegram_webhook_secret
    candidate_secret = supplied_secret or ""
    matches = secrets.compare_digest(
        candidate_secret.encode("utf-8"), expected_secret.encode("utf-8")
    )
    if not expected_secret or not supplied_secret or not matches:
        raise AssistantError(
            ErrorCode.PERMISSION_DENIED,
            "telegram webhook authentication failed",
            tenant_id=settings.tenant_id,
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


def _should_send_audio_reply(
    settings: AppSettings, message: NormalizedMessage, text: str
) -> bool:
    if settings.telegram_audio_reply_mode in {"", "disabled", "none"}:
        return False
    if len(text) > settings.tts_max_reply_characters:
        return False
    if settings.telegram_audio_reply_mode == "always":
        return True
    if settings.telegram_audio_reply_mode in {
        "voice_only",
        "voice-only",
        "audio_only",
        "audio-only",
    }:
        return message.media_kind in {"voice", "audio"}
    return False


def _trace_telegram_audio_reply_failure(
    container: AppContainer,
    principal: Principal,
    *,
    chat_id: str,
    text: str,
    idempotency_key: str,
    stage: str,
    tool_name: str,
    exc: Exception,
) -> None:
    container.traces.write(
        TraceEvent(
            run_id=f"telegram:{chat_id}:{idempotency_key}:audio-reply",
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id=principal.tenant_id,
            input_summary={
                "operation": "telegram.audio_reply",
                "stage": stage,
                "text_length": len(text),
            },
            tool_call={
                "name": tool_name,
                "idempotency_key": f"{idempotency_key}:reply-audio",
            },
            error={
                "type": exc.__class__.__name__,
                "message": str(exc)[:500],
                "category": "audio",
            },
        )
    )


def _send_telegram_audio_reply(
    container: AppContainer,
    principal: Principal,
    settings: AppSettings,
    *,
    chat_id: str,
    text: str,
    idempotency_key: str,
) -> bool:
    if container.tts is None:
        return False
    if len(text) > settings.tts_max_reply_characters:
        return False
    try:
        synthesized = container.tts.synthesize(
            request=AudioSynthesisRequest(
                text=text,
                voice_id=settings.tts_voice_id,
                audio_format=cast(
                    Literal["mp3", "wav", "flac"], settings.tts_audio_format
                ),
                language_boost=settings.tts_language_boost,
            ),
            budget=TokenBudget(limit=settings.tts_max_reply_characters),
        )
    except Exception as exc:
        _trace_telegram_audio_reply_failure(
            container,
            principal,
            chat_id=chat_id,
            text=text,
            idempotency_key=idempotency_key,
            stage="synthesize",
            tool_name="audio.synthesize",
            exc=exc,
        )
        return False

    try:
        request = NotificationRequest(
            channel="telegram",
            recipient=chat_id,
            body=text,
            idempotency_key=f"{idempotency_key}:reply-audio",
            media=NotificationMedia(
                filename=f"assistant-reply.{synthesized.filename_extension}",
                content_type=synthesized.content_type,
                data=synthesized.audio,
            ),
        )
        approval = ApprovalGrant.issue(
            principal=principal,
            action="notification.send",
            resource=request.idempotency_key,
            tier=PermissionTier.P5,
            approval_id=f"{idempotency_key}:reply-audio",
        )
        container.notifications.send(principal, request, approval=approval)
    except Exception as exc:
        _trace_telegram_audio_reply_failure(
            container,
            principal,
            chat_id=chat_id,
            text=text,
            idempotency_key=idempotency_key,
            stage="send",
            tool_name="notification.send",
            exc=exc,
        )
        return False
    return True


def _transcription_filename(message: NormalizedMessage, file_path: str) -> str:
    extension = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if extension == "oga":
        extension = "ogg"
    elif extension not in SUPPORTED_TRANSCRIPTION_EXTENSIONS:
        if message.media_mime_type == "audio/ogg":
            extension = "ogg"
        elif message.media_mime_type == "audio/opus":
            extension = "opus"
        else:
            extension = "ogg"
    return f"telegram-{message.message_id}.{extension}"


def _transcribe_telegram_media(
    container: AppContainer,
    settings: AppSettings,
    message: NormalizedMessage,
    replies: AssistantReplies,
) -> tuple[NormalizedMessage | None, str | None]:
    if not message.media_file_id:
        return None, replies.telegram_audio_missing_file_id()
    if container.transcription is None:
        return None, replies.telegram_transcription_not_configured()
    if not settings.telegram_bot_token:
        return None, replies.telegram_token_missing_for_audio()
    if (
        message.media_file_size is not None
        and message.media_file_size > MAX_TELEGRAM_AUDIO_BYTES
    ):
        return None, replies.telegram_audio_too_large()

    transcription_filename: str | None = None
    telegram_file_extension: str | None = None
    try:
        client = TelegramBotApiClient(token=settings.telegram_bot_token)
        file_info = client.get_file(file_id=message.media_file_id)
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            return None, replies.telegram_file_path_missing()
        audio = client.download_file(file_path=file_path)
        if len(audio) > MAX_TELEGRAM_AUDIO_BYTES:
            return None, replies.telegram_audio_download_too_large()

        telegram_file_extension = (
            file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
        )
        transcription_filename = _transcription_filename(message, file_path)
        transcript = container.transcription.transcribe(
            AudioTranscriptionRequest(
                filename=transcription_filename,
                content_type=message.media_mime_type or "audio/ogg",
                data=audio,
                language="es",
                prompt=container.prompt_catalog.render(
                    "telegram_voice_transcription", {}
                ).text,
            ),
            budget=TokenBudget(limit=4_000),
        )
        container.traces.write(
            TraceEvent(
                run_id=f"telegram:{message.conversation_id}:{message.message_id}:transcription",
                agent_id="personal_assistant",
                event_type=TraceEventType.tool_called,
                tenant_id=settings.tenant_id,
                input_summary={
                    "media_kind": message.media_kind,
                    "media_mime_type": message.media_mime_type,
                    "media_file_size": message.media_file_size,
                    "telegram_file_extension": telegram_file_extension,
                    "transcription_filename": transcription_filename,
                },
                tool_call={"name": "audio.transcribe", "provider": transcript.provider},
                model=transcript.model,
                output_summary={
                    "transcript": transcript.text[:500],
                    "text_length": len(transcript.text),
                },
            )
        )
    except Exception as exc:
        container.traces.write(
            TraceEvent(
                run_id=f"telegram:{message.conversation_id}:{message.message_id}:transcription",
                agent_id="personal_assistant",
                event_type=TraceEventType.agent_failed,
                tenant_id=settings.tenant_id,
                input_summary={
                    "media_kind": message.media_kind,
                    "media_mime_type": message.media_mime_type,
                    "media_file_size": message.media_file_size,
                    "telegram_file_extension": telegram_file_extension,
                    "transcription_filename": transcription_filename,
                },
                error={"type": exc.__class__.__name__, "message": str(exc)[:500]},
            )
        )
        return None, replies.telegram_transcription_failed()
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


def create_app(
    container: AppContainer | None = None, settings: AppSettings | None = None
) -> FastAPI:
    runtime_settings = settings or AppSettings.from_env()
    runtime_container = container or build_runtime_container(runtime_settings)
    runtime_replies = runtime_container.commands.replies
    local_principal_provider = (
        LocalPrincipalProvider.from_settings(runtime_settings)
        if runtime_settings.admin_token is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime_settings.reminder_worker_enabled:
            thread = threading.Thread(
                target=_run_reminder_worker_loop,
                kwargs={
                    "container": runtime_container,
                    "settings": runtime_settings,
                    "stop_event": app.state.reminder_worker_stop,
                },
                name="personal-assistant-reminder-worker",
                daemon=True,
            )
            app.state.reminder_worker_thread = thread
            thread.start()
        try:
            yield
        finally:
            app.state.reminder_worker_stop.set()
            thread = app.state.reminder_worker_thread
            if thread is not None:
                thread.join(timeout=5)

    app = FastAPI(
        title="Personal Assistant Runtime", version="0.1.0", lifespan=lifespan
    )
    app.state.container = runtime_container
    app.state.settings = runtime_settings
    app.state.reminder_worker_stop = threading.Event()
    app.state.reminder_worker_thread = None
    app.state.local_principal_provider = local_principal_provider
    dashboard = AdminDashboard(runtime_container)

    @app.exception_handler(AssistantError)
    async def handle_assistant_error(_: Any, exc: AssistantError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for_error(exc.code),
            content=jsonable_encoder(exc.model_dump()),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Any, exc: RequestValidationError
    ) -> JSONResponse:
        response = error_response(
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            context={"errors": exc.errors()},
        )
        return JSONResponse(
            status_code=422, content=jsonable_encoder(response.model_dump(mode="json"))
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Any, exc: ValidationError) -> JSONResponse:
        response = error_response(
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            context={"errors": exc.errors()},
        )
        return JSONResponse(
            status_code=422, content=jsonable_encoder(response.model_dump(mode="json"))
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Any, exc: ValueError) -> JSONResponse:
        response = error_response(ErrorCode.VALIDATION_FAILED, str(exc))
        return JSONResponse(
            status_code=422, content=jsonable_encoder(response.model_dump(mode="json"))
        )

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
        "/webhooks/telegram",
        response_model=TelegramWebhookResponse,
        tags=["telegram"],
    )
    def telegram_webhook(
        payload: dict[str, Any],
        x_telegram_secret: Annotated[
            str | None,
            Security(TELEGRAM_WEBHOOK_SECRET_HEADER),
        ],
    ) -> TelegramWebhookResponse:
        _require_telegram_webhook_secret(runtime_settings, x_telegram_secret)

        try:
            message = normalize_telegram_webhook(
                payload, tenant_id=runtime_settings.tenant_id
            )
        except TelegramActorNotVerifiableError as exc:
            raise AssistantError(
                ErrorCode.PERMISSION_DENIED,
                "telegram update has no verifiable actor",
                tenant_id=runtime_settings.tenant_id,
            ) from exc
        principal = telegram_principal(runtime_settings, message.actor_id)
        if message.media_kind in {"voice", "audio"}:
            transcribed, transcription_error = _transcribe_telegram_media(
                runtime_container,
                runtime_settings,
                message,
                runtime_replies,
            )
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

        try:
            result = runtime_container.commands.handle(
                principal,
                message,
                now=datetime.now(UTC),
                timezone=runtime_settings.timezone,
            )
        except ReminderIdempotencyConflict:
            # Telegram must acknowledge provider delivery with HTTP 200 while
            # exposing no internal key/fingerprint metadata and performing no
            # reply, calendar, scheduler, event, or outbox side effect.
            return TelegramWebhookResponse(
                status=AgentStatus.failed,
                reply=runtime_replies.reminder_replay_conflict(),
                sent=False,
                audio_sent=False,
                approval_id=None,
                command=message.command,
            )
        sent = False
        audio_sent = False
        if runtime_settings.telegram_bot_token:
            sent = _send_telegram_reply(
                runtime_container,
                principal,
                chat_id=message.conversation_id,
                text=result.reply,
                idempotency_key=message.idempotency_key
                or f"telegram:{message.conversation_id}:{message.message_id}",
            )
            if sent and _should_send_audio_reply(
                runtime_settings, message, result.reply
            ):
                audio_sent = _send_telegram_audio_reply(
                    runtime_container,
                    principal,
                    runtime_settings,
                    chat_id=message.conversation_id,
                    text=result.reply,
                    idempotency_key=message.idempotency_key
                    or f"telegram:{message.conversation_id}:{message.message_id}",
                )
        return TelegramWebhookResponse(
            status=result.status,
            reply=result.reply,
            sent=sent,
            audio_sent=audio_sent,
            approval_id=result.approval_id,
            command=message.command,
        )

    @app.get("/admin", response_class=HTMLResponse, tags=["admin"])
    def admin_page(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> HTMLResponse:
        return HTMLResponse(dashboard.render_html(principal, limit=clamp_limit(limit)))

    @app.get("/admin/snapshot", tags=["admin"])
    def admin_snapshot(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.snapshot(principal, limit=clamp_limit(limit))

    @app.get("/admin/health", tags=["admin"])
    def admin_health(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.snapshot(principal, limit=clamp_limit(limit))["health"]

    @app.get("/admin/approvals", tags=["admin"])
    def admin_approvals(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.approvals(principal, limit=clamp_limit(limit))

    @app.get("/admin/traces", tags=["admin"])
    def admin_traces(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.traces(principal, limit=clamp_limit(limit))

    @app.get("/admin/outbox", tags=["admin"])
    def admin_outbox(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.outbox(principal, limit=clamp_limit(limit))

    @app.get("/admin/scheduler", tags=["admin"])
    def admin_scheduler(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.scheduler(principal, limit=clamp_limit(limit))

    @app.get("/admin/agenda", tags=["admin"])
    def admin_agenda(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.agenda(principal, limit=clamp_limit(limit))

    @app.get("/admin/reminders", tags=["admin"])
    def admin_reminders(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.reminders(principal, limit=clamp_limit(limit))

    @app.get("/admin/errors", tags=["admin"])
    def admin_errors(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        category: Annotated[str | None, Query(min_length=1)] = None,
        run_id: Annotated[str | None, Query(min_length=1)] = None,
        event_type: Annotated[str | None, Query(min_length=1)] = None,
        source: Annotated[str | None, Query(min_length=1)] = None,
    ) -> dict[str, Any]:
        return dashboard.errors(
            principal,
            category=category,
            run_id=run_id,
            event_type=event_type,
            source=source,
            limit=clamp_limit(limit),
        )

    @app.get("/admin/events", tags=["admin"])
    def admin_events(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.events(principal, limit=clamp_limit(limit))

    @app.get("/admin/states", tags=["admin"])
    def admin_states(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return dashboard.states(principal, limit=clamp_limit(limit))

    @app.get("/admin/memory", tags=["admin"])
    def admin_memory(
        principal: Annotated[Principal, Depends(current_principal)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
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
        result = runtime_container.reminder_workflow.run(
            principal, request.to_workflow_input()
        )
        approval_view: ApprovalView | None = None
        if result.approval_required:
            response.status_code = 202
            action = "calendar.create_event"
            pending = runtime_container.approvals.create(
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
                reason=runtime_replies.approval_reason_calendar_create_event(),
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
        pending_approvals = runtime_container.approvals.list_for_tenant(principal)
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
                reason=runtime_replies.approval_reason_calendar_create_event(),
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
        pending = runtime_container.approvals.get(principal, approval_id)
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

        grant = runtime_container.approvals.approve(principal, approval_id)
        result = runtime_container.reminder_workflow.run(
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
        existing = runtime_container.approvals.get(principal, approval_id)
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
        pending = runtime_container.approvals.reject(principal, approval_id)
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
            return runtime_container.traces.list_for_run(principal, run_id)
        return runtime_container.traces.list_for_tenant(principal)

    return app


app = create_app()
