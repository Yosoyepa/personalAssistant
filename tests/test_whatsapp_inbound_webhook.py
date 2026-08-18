"""Integration and unit tests for WhatsApp Cloud API inbound webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from personal_assistant.domain.common.exceptions import AssistantError
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings
from personal_assistant.infrastructure.http import create_app
from personal_assistant.infrastructure.http_auth_whatsapp import (
    verify_whatsapp_signature,
    whatsapp_principal,
)


def _sign_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_whatsapp_payload(
    wa_id: str,
    text: str,
    message_id: str = "wamid.HBgLNTczMDAxMTIyMzM",
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100000000000001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001",
                                "phone_number_id": "100000000000002",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": wa_id,
                                }
                            ],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": message_id,
                                    "timestamp": "1723654800",
                                    "text": {"body": text},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def _make_status_payload() -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100000000000001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001",
                                "phone_number_id": "100000000000002",
                            },
                            "statuses": [
                                {
                                    "id": "wamid.HBgLNTczMDAxMTIyMzM",
                                    "status": "delivered",
                                    "timestamp": "1723654805",
                                    "recipient_id": "573001112233",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def whatsapp_settings() -> AppSettings:
    return AppSettings(
        tenant_id="personal",
        timezone="America/Bogota",
        reply_locale="es",
        persistence_backend="memory",
        whatsapp=WhatsAppSettings(
            enabled=True,
            app_secret="test-whatsapp-secret-key-12345",
            verify_token="test-verify-token-abcde",
            allowed_user_ids=frozenset({"573001112233", "573004445566"}),
        ),
    )


@pytest.fixture
def test_client(whatsapp_settings: AppSettings) -> TestClient:
    container = build_container(settings=whatsapp_settings)
    app = create_app(container=container, settings=whatsapp_settings)
    return TestClient(app)


def test_whatsapp_verify_webhook_success(test_client: TestClient) -> None:
    response = test_client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token-abcde",
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "1158201444"


def test_whatsapp_verify_webhook_wrong_token(test_client: TestClient) -> None:
    response = test_client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 403


def test_whatsapp_verify_webhook_invalid_mode(test_client: TestClient) -> None:
    response = test_client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "publish",
            "hub.verify_token": "test-verify-token-abcde",
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 403


def test_whatsapp_verify_webhook_works_when_channel_disabled() -> None:
    disabled_settings = AppSettings(
        tenant_id="personal",
        persistence_backend="memory",
        whatsapp=WhatsAppSettings(
            enabled=False,
            app_secret="test-secret",
            verify_token="test-verify-token-abcde",
            allowed_user_ids=frozenset({"573001112233"}),
        ),
    )
    container = build_container(settings=disabled_settings)
    client = TestClient(create_app(container=container, settings=disabled_settings))
    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token-abcde",
            "hub.challenge": "99887766",
        },
    )
    assert response.status_code == 200
    assert response.text == "99887766"


def test_whatsapp_webhook_post_channel_disabled() -> None:
    disabled_settings = AppSettings(
        tenant_id="personal",
        persistence_backend="memory",
        whatsapp=WhatsAppSettings(
            enabled=False,
            app_secret="test-secret",
            verify_token="test-token",
            allowed_user_ids=frozenset({"573001112233"}),
        ),
    )
    container = build_container(settings=disabled_settings)
    client = TestClient(create_app(container=container, settings=disabled_settings))
    payload = _make_whatsapp_payload("573001112233", "hello")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, "test-secret")
    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert response.status_code == 403


def test_whatsapp_webhook_post_invalid_signature(test_client: TestClient) -> None:
    payload = _make_whatsapp_payload("573001112233", "remind me to call Ana at 5 pm")
    body = json.dumps(payload).encode("utf-8")
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={
            "x-hub-signature-256": "sha256=invalidhexsignature000000000000000000",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 401


def test_whatsapp_webhook_post_missing_signature(test_client: TestClient) -> None:
    payload = _make_whatsapp_payload("573001112233", "hello")
    body = json.dumps(payload).encode("utf-8")
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401


def test_whatsapp_webhook_post_status_only_callback(
    test_client: TestClient, whatsapp_settings: AppSettings
) -> None:
    payload = _make_status_payload()
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, whatsapp_settings.whatsapp.app_secret)
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["reply"] is None
    assert data["sent"] is False


def test_whatsapp_webhook_post_valid_command(
    test_client: TestClient, whatsapp_settings: AppSettings
) -> None:
    payload = _make_whatsapp_payload(
        "573001112233",
        "remind me to call Ana at 5 pm",
        message_id="wamid.test001",
    )
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, whatsapp_settings.whatsapp.app_secret)
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sent"] is False
    assert data["reply"] is not None
    assert len(data["reply"]) > 0


def test_whatsapp_webhook_post_unauthorized_sender(
    test_client: TestClient, whatsapp_settings: AppSettings
) -> None:
    payload = _make_whatsapp_payload(
        "573009999999",
        "remind me to call Bob tomorrow",
        message_id="wamid.test002",
    )
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, whatsapp_settings.whatsapp.app_secret)
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert response.status_code == 403
    # Ensure raw PII / wa_id is not exposed in public error message
    assert "573009999999" not in response.text


def test_whatsapp_webhook_post_replayed_message_id(
    test_client: TestClient, whatsapp_settings: AppSettings
) -> None:
    payload = _make_whatsapp_payload(
        "573001112233",
        "remind me to pay bills tomorrow at 10 am",
        message_id="wamid.replay001",
    )
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, whatsapp_settings.whatsapp.app_secret)

    # First delivery
    res1 = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert res1.status_code == 200
    assert res1.json()["sent"] is False

    # Second delivery (replay)
    res2 = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert res2.status_code == 200
    assert res2.json()["sent"] is False


def test_verify_whatsapp_signature_helpers() -> None:
    settings = WhatsAppSettings(enabled=True, app_secret="secret123")
    body = b'{"hello": "world"}'
    valid_sig = _sign_payload(body, "secret123")

    assert verify_whatsapp_signature(settings, body, valid_sig) is True
    assert verify_whatsapp_signature(settings, body, "sha256=invalid") is False
    assert verify_whatsapp_signature(settings, body, None) is False
    assert verify_whatsapp_signature(settings, body, "badprefix") is False

    empty_secret_settings = WhatsAppSettings(enabled=True, app_secret="")
    assert verify_whatsapp_signature(empty_secret_settings, body, valid_sig) is False


def test_whatsapp_principal_resolution() -> None:
    settings = WhatsAppSettings(
        enabled=True,
        allowed_user_ids=frozenset({"573001112233"}),
    )
    principal = whatsapp_principal(settings, "573001112233", tenant_id="tenant-1")
    assert principal.principal_id == "573001112233"
    assert principal.tenant_id == "tenant-1"
    assert principal.permission_tier == PermissionTier.P5
    assert principal.auth_provider == "whatsapp"

    with pytest.raises(AssistantError):
        whatsapp_principal(settings, "disallowed_user", tenant_id="tenant-1")


def test_whatsapp_webhook_post_with_access_token_delivers_reply() -> None:
    from personal_assistant.adapters.outbound.notifications.whatsapp import (
        WhatsAppNotificationTool,
    )
    from tests.test_whatsapp_outbound import FakeWhatsAppClient

    settings = AppSettings(
        tenant_id="personal",
        timezone="America/Bogota",
        reply_locale="es",
        persistence_backend="memory",
        whatsapp=WhatsAppSettings(
            enabled=True,
            app_secret="test-secret",
            verify_token="test-token",
            allowed_user_ids=frozenset({"573001112233"}),
            access_token="test-access-token",
            phone_number_id="100000000000002",
        ),
    )
    client = FakeWhatsAppClient()
    tool = WhatsAppNotificationTool(client)
    container = build_container(settings=settings, notifications=tool)
    app = create_app(container=container, settings=settings)
    test_client = TestClient(app)

    payload = _make_whatsapp_payload(
        "573001112233",
        "remind me to call Ana at 5 pm",
        message_id="wamid.outbound001",
    )
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, settings.whatsapp.app_secret)
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sent"] is True
    assert data["reply"] is not None
    assert len(client.sent) == 1
    assert client.sent[0]["recipient"] == "573001112233"


def test_whatsapp_webhook_post_reply_failure_still_returns_200_with_sent_false() -> (
    None
):
    from personal_assistant.adapters.outbound.notifications.whatsapp import (
        WhatsAppNotificationTool,
        WhatsAppProviderResult,
    )
    from tests.test_whatsapp_outbound import OutcomeWhatsAppClient

    settings = AppSettings(
        tenant_id="personal",
        timezone="America/Bogota",
        reply_locale="es",
        persistence_backend="memory",
        whatsapp=WhatsAppSettings(
            enabled=True,
            app_secret="test-secret",
            verify_token="test-token",
            allowed_user_ids=frozenset({"573001112233"}),
            access_token="test-access-token",
            phone_number_id="100000000000002",
        ),
    )
    client = OutcomeWhatsAppClient(
        WhatsAppProviderResult(outcome="known-transient", provider_code=500)
    )
    tool = WhatsAppNotificationTool(client)
    container = build_container(settings=settings, notifications=tool)
    app = create_app(container=container, settings=settings)
    test_client = TestClient(app)

    payload = _make_whatsapp_payload(
        "573001112233",
        "remind me to call Ana at 5 pm",
        message_id="wamid.outbound002",
    )
    body = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(body, settings.whatsapp.app_secret)
    response = test_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sent"] is False
    assert data["reply"] is not None


def test_send_whatsapp_reply_helper_cases() -> None:
    from personal_assistant.infrastructure.http_whatsapp_replies import (
        _send_whatsapp_reply,
    )

    settings_no_token = WhatsAppSettings(enabled=True, access_token=None)
    container_no_notif = build_container()
    object.__setattr__(container_no_notif, "notifications", None)
    p = Principal.for_test(
        principal_id="p1", tenant_id="t1", permission_tier=PermissionTier.P5
    )

    assert (
        _send_whatsapp_reply(
            container_no_notif,
            p,
            settings_no_token,
            recipient="123",
            text="hi",
            idempotency_key="k1",
        )
        is False
    )

    settings_with_token = WhatsAppSettings(enabled=True, access_token="tok")
    assert (
        _send_whatsapp_reply(
            container_no_notif,
            p,
            settings_with_token,
            recipient="123",
            text="",
            idempotency_key="k1",
        )
        is False
    )
    assert (
        _send_whatsapp_reply(
            container_no_notif,
            p,
            settings_with_token,
            recipient="123",
            text="hi",
            idempotency_key="k1",
        )
        is False
    )
