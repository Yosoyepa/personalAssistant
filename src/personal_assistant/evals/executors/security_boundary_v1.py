"""Executable probes of the application's real security boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal, cast
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, model_validator

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.domain.common.privacy import REDACTED, REDACTED_URL
from personal_assistant.infrastructure.bootstrap import AppContainer, build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.http import create_app

_SECRET = "test_webhook_secret"
_TOKEN = "test_local_token"
_TENANT = "fixture-tenant"
_PRINCIPAL = "fixture-user"

_VARIANTS = {
    "local-reject": {
        "missing-runtime",
        "wrong-runtime",
        "basic-runtime",
        "malformed-runtime",
        "legacy-runtime",
        "remote-runtime",
        "missing-admin",
        "wrong-admin",
        "basic-admin",
        "legacy-admin",
        "remote-admin",
        "unconfigured-runtime",
    },
    "local-authority": {
        "forged-headers",
        "forged-query",
        "forwarded-remote",
        "fixed-principal",
    },
    "webhook-reject": {
        "missing-secret",
        "wrong-secret",
        "unconfigured-secret",
        "empty-allowlist",
        "denied-actor",
        "missing-actor",
    },
    "webhook-allow": {"help", "p3-reminder"},
    "trace-redaction": {
        "message",
        "transcript",
        "token",
        "url",
        "audio",
        "error",
        "authorization",
        "unknown",
        "nested",
        "binary",
        "run-id",
        "idempotent",
        "output",
        "query",
        "metadata",
        "tool-call",
        "validation",
        "context-ref",
        "safe-metric",
        "safe-category",
        "safe-list",
        "error-context",
        "output-url",
        "input-list",
        "tool-url",
        "validation-list",
    },
}


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario: Literal[
        "local-reject",
        "local-authority",
        "webhook-reject",
        "webhook-allow",
        "trace-redaction",
    ]
    variant: str

    @model_validator(mode="after")
    def valid_variant(self) -> "InputModel":
        if self.variant not in _VARIANTS[self.scenario]:
            raise ValueError("variant is not valid for scenario")
        return self


class EffectCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approvals: int
    calendar: int
    scheduler: int
    events: int
    outbox: int
    states: int
    traces: int


class Authority(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant: str
    principal: str
    tier: str
    provider: str


class ResponseSafety(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    secret_leaked: bool
    pii_leaked: bool
    command: str | None


class RedactionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    redacted: int
    redacted_urls: int
    binary: int
    unsafe_present: bool
    omitted: int


class ExpectedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: int
    error: str | None
    effects: EffectCounts
    authority: Authority | None
    response: ResponseSafety
    redaction: RedactionMetrics | None


def _settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "tenant_id": _TENANT,
        "admin_token": _TOKEN,
        "local_auth_principal_id": _PRINCIPAL,
        "local_auth_permission_tier": PermissionTier.P5,
        "telegram_webhook_secret": _SECRET,
        "telegram_allowed_user_ids": frozenset({"456"}),
        "reminder_worker_enabled": False,
    }
    values.update(overrides)
    return AppSettings(**values)  # type: ignore[arg-type]


def _principal() -> Principal:
    return Principal.for_test(
        tenant_id=_TENANT, principal_id=_PRINCIPAL, permission_tier=PermissionTier.P5
    )


def _counts(
    container: AppContainer, principal: Principal | None = None
) -> EffectCounts:
    principal = principal or _principal()
    return EffectCounts(
        approvals=len(container.approvals.list_for_tenant(principal)),
        calendar=len(container.calendar.list_events(principal)),
        scheduler=len(container.scheduler.list_for_tenant(principal)),
        events=len(container.event_store.list_for_tenant(principal)),
        outbox=len(container.outbox.list_for_tenant(principal)),
        states=len(container.states.list_for_tenant(principal)),
        traces=len(container.traces.list_for_tenant(principal)),
    )


def _runtime_payload() -> dict[str, object]:
    return {
        "message_id": "fixture-message",
        "source_event_id": "fixture-event",
        "conversation_id": "fixture-chat",
        "text": "recordarme manana a las 17 cerrar caja",
        "channel": "telegram",
        "recipient": "fixture-chat",
        "now": "2026-06-20T12:00:00+00:00",
        "timezone": "America/Bogota",
    }


def _response_safety(
    response: object,
    command: str | None = None,
    *,
    secret_sentinels: tuple[str, ...] = (),
    pii_sentinels: tuple[str, ...] = (),
) -> ResponseSafety:
    text = getattr(response, "text")
    return ResponseSafety(
        secret_leaked=any(
            sentinel in text for sentinel in (_SECRET, _TOKEN, *secret_sentinels)
        ),
        pii_leaked=any(sentinel in text for sentinel in pii_sentinels),
        command=command,
    )


def _local_reject(variant: str) -> ExpectedModel:
    container = build_container()
    settings = (
        _settings(admin_token=None)
        if variant == "unconfigured-runtime"
        else _settings()
    )
    remote = variant == "remote-runtime" or variant == "remote-admin"
    client = TestClient(
        create_app(container, settings=settings),
        client=(("203.0.113.8" if remote else "127.0.0.1"), 50000),
    )
    surface = "admin" if variant.endswith("admin") else "runtime"
    headers: dict[str, str] = {}
    if variant.startswith("wrong"):
        headers["Authorization"] = "Bearer wrong-token"
    elif variant.startswith("basic"):
        headers["Authorization"] = f"Basic {_TOKEN}"
    elif variant.startswith("malformed"):
        headers["Authorization"] = f"Bearer {_TOKEN} extra"
    elif variant.startswith("legacy"):
        headers["X-Admin-Token"] = _TOKEN
    elif variant.startswith("remote"):
        headers = {
            "Authorization": f"Bearer {_TOKEN}",
            "Forwarded": "for=127.0.0.1",
            "X-Forwarded-For": "127.0.0.1",
        }
    elif variant == "unconfigured-runtime":
        headers["Authorization"] = f"Bearer {_TOKEN}"
    response = (
        client.get("/admin/health", headers=headers)
        if surface == "admin"
        else client.post(
            "/v1/runtime/reminders", headers=headers, json=_runtime_payload()
        )
    )
    body = response.json()
    return ExpectedModel(
        status=response.status_code,
        error=body["error"]["code"],
        effects=_counts(container),
        authority=None,
        response=_response_safety(
            response,
            secret_sentinels=("wrong-token",),
            pii_sentinels=(
                "fixture-message",
                "fixture-chat",
                "recordarme manana a las 17 cerrar caja",
                "attacker",
                "victim",
                "P6",
            ),
        ),
        redaction=None,
    )


def _local_authority(variant: str) -> ExpectedModel:
    container = build_container()
    recorded: list[Principal] = []
    delegate = container.reminder_workflow

    class RecordingWorkflow:
        def run(self, principal: Principal, request: Any) -> Any:
            recorded.append(principal)
            return delegate.run(principal, request)

    container.reminder_workflow = RecordingWorkflow()  # type: ignore[assignment]
    peer = "203.0.113.8" if variant == "forwarded-remote" else "127.0.0.1"
    client = TestClient(
        create_app(container, settings=_settings()), client=(peer, 50000)
    )
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    suffix = ""
    if variant == "forged-headers":
        headers.update(
            {
                "X-Principal-Id": "attacker",
                "X-Tenant-Id": "victim",
                "X-Permission-Tier": "P6",
            }
        )
    elif variant == "forged-query":
        suffix = "?tenant_id=victim&principal_id=attacker&permission_tier=P6"
    elif variant == "forwarded-remote":
        headers.update({"Forwarded": "for=127.0.0.1", "X-Forwarded-For": "127.0.0.1"})
    response = client.post(
        f"/v1/runtime/reminders{suffix}", headers=headers, json=_runtime_payload()
    )
    if variant == "forwarded-remote":
        return ExpectedModel(
            status=response.status_code,
            error=response.json()["error"]["code"],
            effects=_counts(container),
            authority=None,
            response=_response_safety(
                response, pii_sentinels=("attacker", "victim", "P6")
            ),
            redaction=None,
        )
    [principal] = recorded
    return ExpectedModel(
        status=response.status_code,
        error=None,
        effects=_counts(container),
        authority=Authority(
            tenant=principal.tenant_id,
            principal=principal.principal_id,
            tier=principal.permission_tier.value,
            provider="local-bearer",
        ),
        response=_response_safety(response, pii_sentinels=("attacker", "victim", "P6")),
        redaction=None,
    )


def _webhook_payload(actor: str | None, *, reminder: bool = False) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": 42,
        "chat": {"id": "fixture-chat"},
        "text": "/recordar recuérdame mañana a las 17 cerrar caja"
        if reminder
        else "/help",
    }
    if actor is not None:
        message["from"] = {"id": actor}
    return {"update_id": 99, "message": message}


def _webhook_reject(variant: str) -> ExpectedModel:
    secret, allowlist, headers = (
        _SECRET,
        frozenset({"456"}),
        {"X-Telegram-Bot-Api-Secret-Token": _SECRET},
    )
    actor: str | None = "456"
    if variant == "missing-secret":
        headers = {}
    elif variant == "wrong-secret":
        headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}
    elif variant == "unconfigured-secret":
        secret = ""
    elif variant == "empty-allowlist":
        allowlist = frozenset()
    elif variant == "denied-actor":
        actor = "999"
    elif variant == "missing-actor":
        actor = None
    container = build_container()
    client = TestClient(
        create_app(
            container,
            settings=_settings(
                telegram_webhook_secret=secret, telegram_allowed_user_ids=allowlist
            ),
        )
    )
    response = client.post(
        "/webhooks/telegram", headers=headers, json=_webhook_payload(actor)
    )
    return ExpectedModel(
        status=response.status_code,
        error=response.json()["error"]["code"],
        effects=_counts(container),
        authority=None,
        response=_response_safety(
            response,
            secret_sentinels=("wrong-secret",),
            pii_sentinels=("fixture-chat", "456", "999", "/help"),
        ),
        redaction=None,
    )


def _webhook_allow(variant: str) -> ExpectedModel:
    container = build_container()
    recorded: list[Principal] = []
    delegate = container.commands

    class RecordingCommands:
        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

        def handle(self, principal: Principal, message: Any, **kwargs: Any) -> Any:
            recorded.append(principal)
            return delegate.handle(principal, message, **kwargs)

    container.commands = RecordingCommands()  # type: ignore[assignment]
    client = TestClient(create_app(container, settings=_settings()))
    if variant == "p3-reminder":
        frozen = datetime(2026, 6, 20, 12, tzinfo=UTC)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> "FrozenDateTime":
                return cast(
                    "FrozenDateTime",
                    frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None),
                )

        with patch("personal_assistant.infrastructure.http.datetime", FrozenDateTime):
            response = client.post(
                "/webhooks/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
                json=_webhook_payload("456", reminder=True),
            )
    else:
        response = client.post(
            "/webhooks/telegram",
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            json=_webhook_payload("456"),
        )
    body = response.json()
    [principal] = recorded
    approvals = container.approvals.list_for_tenant(principal)
    # The pending approval is the persisted authority-bearing artifact.
    if approvals:
        authority = Authority(
            tenant=approvals[0].tenant_id,
            principal=approvals[0].principal_id,
            tier=principal.permission_tier.value,
            provider="telegram",
        )
    else:
        authority = Authority(
            tenant=principal.tenant_id,
            principal=principal.principal_id,
            tier=principal.permission_tier.value,
            provider="telegram",
        )
    return ExpectedModel(
        status=response.status_code,
        error=None,
        effects=_counts(container, principal),
        authority=authority,
        response=_response_safety(
            response,
            body.get("command"),
            pii_sentinels=("fixture-chat", "456", "recuérdame"),
        ),
        redaction=None,
    )


def _trace_redaction(variant: str) -> ExpectedModel:
    secret, message = f"fixture-{variant}-secret", f"fixture-{variant}-message"
    url = f"https://fixture-user:{secret}@example.invalid/path?access_token={secret}"
    nested = {
        "message": message,
        "authorization": f"Bearer {secret}",
        "request_url": url,
        "audio": b"audio",
        "unknown_future_field": message,
    }
    fields: dict[str, object] = {"message": message}
    if variant == "transcript":
        fields = {"transcript": message}
    elif variant == "token":
        fields = {"token": secret}
    elif variant == "url":
        fields = {"request_url": url}
    elif variant == "query":
        fields = {"query": f"access_token={secret}"}
    elif variant == "audio":
        fields = {"audio": b"audio"}
    elif variant == "binary":
        fields = {"attachment": b"different-binary"}
    elif variant == "error":
        fields = {"message": message, "token": secret}
    elif variant == "authorization":
        fields = {"authorization": f"Bearer {secret}"}
    elif variant == "unknown":
        fields = {"unknown_future_field": message}
    elif variant == "nested":
        fields = {"metadata": {"items": [nested]}}
    elif variant == "run-id":
        fields = {"message": message}
    elif variant == "output":
        fields = {"output_message": message}
    elif variant == "metadata":
        fields = {"metadata": nested}
    elif variant == "tool-call":
        fields = {"tool_call": nested}
    elif variant == "validation":
        fields = {"validation": nested}
    elif variant == "context-ref":
        fields = {"context_refs": [url]}
    elif variant == "safe-metric":
        fields = {"duration_ms": 12}
    elif variant == "safe-category":
        fields = {"category": "safe"}
    elif variant == "safe-list":
        fields = {"tags": ["safe", "also-safe"]}
    elif variant == "error-context":
        fields = {"context": nested}
    elif variant == "output-url":
        fields = {"output_url": url}
    elif variant == "input-list":
        fields = {"items": [message, secret]}
    elif variant == "tool-url":
        fields = {"tool_url": url}
    elif variant == "validation-list":
        fields = {"errors": [nested]}
    kwargs: dict[str, Any] = {
        "trace_id": "trace-fixture",
        "run_id": url if variant == "run-id" else "run-fixture",
        "agent_id": "personal_assistant",
        "event_type": TraceEventType.agent_failed,
        "tenant_id": _TENANT,
        "input_summary": fields,
    }
    if variant == "transcript":
        kwargs["output_summary"] = fields
    if variant == "error":
        kwargs["error"] = fields
    if variant == "tool-call":
        kwargs["tool_call"] = nested
    if variant == "validation":
        kwargs["validation"] = nested
    if variant == "context-ref":
        kwargs["context_refs"] = [url]
    if variant == "error-context":
        kwargs["error"] = fields
    if variant == "output-url":
        kwargs["output_summary"] = fields
    if variant == "tool-url":
        kwargs["tool_call"] = fields
    if variant == "validation-list":
        kwargs["validation"] = fields
    trace = TraceEvent(**kwargs)
    recorder = TraceRecorder()
    recorder.write(trace)
    if variant == "idempotent":
        recorder.write(trace.for_persistence())
    payload = json.dumps(
        recorder.list_for_tenant(_TENANT)[0].model_dump(mode="json"), sort_keys=True
    )
    metrics = RedactionMetrics(
        redacted=payload.count(REDACTED),
        redacted_urls=payload.count(REDACTED_URL),
        binary=payload.count('"kind": "binary"'),
        unsafe_present=any(
            value in payload for value in (secret, message, "fixture-user")
        ),
        omitted=int(
            variant
            in {
                "unknown",
                "nested",
                "metadata",
                "tool-call",
                "validation",
                "error-context",
                "validation-list",
            }
            and "unknown_future_field" not in payload
        ),
    )
    return ExpectedModel(
        status=200,
        error=None,
        effects=EffectCounts(
            approvals=0,
            calendar=0,
            scheduler=0,
            events=0,
            outbox=0,
            states=0,
            traces=len(recorder.list_for_tenant(_TENANT)),
        ),
        authority=None,
        response=ResponseSafety(secret_leaked=False, pii_leaked=False, command=None),
        redaction=metrics,
    )


def execute(input_model: InputModel) -> dict[str, object]:
    if input_model.scenario == "local-reject":
        result = _local_reject(input_model.variant)
    elif input_model.scenario == "local-authority":
        result = _local_authority(input_model.variant)
    elif input_model.scenario == "webhook-reject":
        result = _webhook_reject(input_model.variant)
    elif input_model.scenario == "webhook-allow":
        result = _webhook_allow(input_model.variant)
    else:
        result = _trace_redaction(input_model.variant)
    return result.model_dump(mode="json")
