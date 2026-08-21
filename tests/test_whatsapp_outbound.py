"""Unit and integration tests for WhatsApp outbound delivery and Graph API client."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import unittest
from datetime import UTC, datetime
from email.message import Message
from http.client import IncompleteRead
from typing import Any, Self
from unittest.mock import patch
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient

from personal_assistant.adapters.outbound.egress import (
    DEFAULT_WHATSAPP_API_URL,
    EgressAllowlist,
    EgressNotAllowedError,
    derive_egress_entries,
)
from personal_assistant.adapters.outbound.notifications.router import (
    ChannelNotificationRouter,
)
from personal_assistant.adapters.outbound.notifications.whatsapp import (
    WhatsAppGraphApiClient,
    WhatsAppNotificationTool,
    WhatsAppProviderResult,
)
from personal_assistant.application.dto.events import CloudEvent, OutboxStatus
from personal_assistant.application.ports.notifications import (
    NotificationRequest,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier
from personal_assistant.infrastructure.bootstrap import build_container
from personal_assistant.infrastructure.config import AppSettings
from personal_assistant.infrastructure.config_constants import (
    DEFAULT_WHATSAPP_API_URL as CONFIG_DEFAULT_WHATSAPP_API_URL,
)
from personal_assistant.infrastructure.config_loader_whatsapp import (
    _load_whatsapp_kwargs,
)
from personal_assistant.infrastructure.config_whatsapp import WhatsAppSettings
from personal_assistant.infrastructure.http import create_app
from personal_assistant.infrastructure.http_container import build_runtime_container
from personal_assistant.infrastructure.worker import _runtime
from personal_assistant.infrastructure.worker_runtime import (
    RuntimeNotificationApprovalPolicy,
)


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def now(self) -> datetime:
        return self.current


class FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_message(self, *, recipient: str, text: str) -> WhatsAppProviderResult:
        self.sent.append({"recipient": recipient, "text": text})
        return WhatsAppProviderResult(
            outcome="success",
            notification_id=f"wamid.test_{len(self.sent)}",
        )


class OutcomeWhatsAppClient:
    def __init__(self, *outcomes: WhatsAppProviderResult) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def send_message(self, *, recipient: str, text: str) -> WhatsAppProviderResult:
        self.calls += 1
        return self.outcomes.pop(0)


class FakeHttpResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers = Message()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class TrackingBody(io.BytesIO):
    def __init__(self, value: bytes = b"") -> None:
        super().__init__(value)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


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


class WhatsAppConfigOutboundTests(unittest.TestCase):
    def test_whatsapp_settings_dataclass_fields(self) -> None:
        settings = WhatsAppSettings(
            enabled=True,
            app_secret="test-secret",
            verify_token="test-verify",
            allowed_user_ids=frozenset({"573001112233"}),
            access_token="test-access-token",
            phone_number_id="100000000000002",
        )
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.access_token, "test-access-token")
        self.assertEqual(settings.phone_number_id, "100000000000002")
        # access_token must have repr=False for security
        self.assertNotIn("test-access-token", repr(settings))

    def test_whatsapp_loader_loads_outbound_env_vars(self) -> None:
        file_values = {
            "WHATSAPP_ENABLED": "true",
            "WHATSAPP_APP_SECRET": "secret",
            "WHATSAPP_VERIFY_TOKEN": "verify",
            "WHATSAPP_ALLOWED_USER_IDS": "573001112233",
            "WHATSAPP_ACCESS_TOKEN": "EAAX12345",
            "WHATSAPP_PHONE_NUMBER_ID": "10987654321",
        }
        loaded = _load_whatsapp_kwargs(file_values)
        self.assertEqual(loaded.access_token, "EAAX12345")
        self.assertEqual(loaded.phone_number_id, "10987654321")

    def test_whatsapp_egress_constants_and_derivation(self) -> None:
        self.assertEqual(CONFIG_DEFAULT_WHATSAPP_API_URL, "https://graph.facebook.com")
        self.assertEqual(DEFAULT_WHATSAPP_API_URL, "https://graph.facebook.com")

        derived_with_token = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url=None,
            tts_base_url=None,
            telegram_bot_token_configured=False,
            whatsapp_access_token_configured=True,
        )
        self.assertIn("https://graph.facebook.com", derived_with_token)

        derived_without_token = derive_egress_entries(
            llm_base_url=None,
            transcription_base_url=None,
            tts_base_url=None,
            telegram_bot_token_configured=False,
            whatsapp_access_token_configured=False,
        )
        self.assertNotIn("https://graph.facebook.com", derived_without_token)

    def test_app_settings_validation_requires_whatsapp_egress_coverage(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            AppSettings(
                tenant_id="personal",
                persistence_backend="memory",
                egress_allowed_hosts=frozenset({"api.telegram.org"}),
                whatsapp=WhatsAppSettings(
                    enabled=True,
                    access_token="secret-token",
                    phone_number_id="12345",
                ),
            )
        self.assertIn("WHATSAPP_ACCESS_TOKEN", str(ctx.exception))
        self.assertIn("graph.facebook.com", str(ctx.exception))


class WhatsAppNotificationToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeWhatsAppClient()
        self.tool = WhatsAppNotificationTool(self.client)
        self.principal = Principal.for_test(
            principal_id="whatsapp-user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.request = NotificationRequest(
            channel="whatsapp",
            recipient="573001112233",
            body="Hola! Tu recordatorio.",
            idempotency_key="wa-msg-1",
        )

    def approval(self) -> ApprovalGrant:
        return ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=self.request.idempotency_key,
            tier=PermissionTier.P5,
        )

    def test_whatsapp_send_requires_p5_approval_before_dispatch_or_replay(self) -> None:
        with self.assertRaises(AssistantError) as missing:
            self.tool.send(self.principal, self.request)

        self.assertEqual(missing.exception.code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(self.client.sent, [])

        sent = self.tool.send(self.principal, self.request, approval=self.approval())
        with self.assertRaises(AssistantError):
            self.tool.send(self.principal, self.request)
        reused = self.tool.send(self.principal, self.request, approval=self.approval())

        self.assertEqual(sent.notification_id, reused.notification_id)
        self.assertEqual(sent.outcome, "success")
        self.assertEqual(sent.notification_id, "whatsapp:wamid.test_1")
        self.assertTrue(reused.reused)
        self.assertEqual(len(self.client.sent), 1)
        self.assertNotIn("recipient", sent.model_dump())

    def test_whatsapp_send_rejects_wrong_channel(self) -> None:
        wrong = self.request.model_copy(update={"channel": "telegram"})
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=wrong.idempotency_key,
            tier=PermissionTier.P5,
        )
        with self.assertRaises(AssistantError) as ctx:
            self.tool.send(self.principal, wrong, approval=approval)
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_FAILED)

    def test_whatsapp_send_rejects_idempotency_conflict(self) -> None:
        self.tool.send(self.principal, self.request, approval=self.approval())
        conflict = self.request.model_copy(update={"body": "Otro texto"})

        with self.assertRaises(AssistantError) as ctx:
            self.tool.send(self.principal, conflict, approval=self.approval())

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(len(self.client.sent), 1)

    def test_whatsapp_unknown_outcome_is_saved_as_terminal(self) -> None:
        client = OutcomeWhatsAppClient(
            WhatsAppProviderResult(outcome="unknown-outcome")
        )
        tool = WhatsAppNotificationTool(client)

        first = tool.send(self.principal, self.request, approval=self.approval())
        replay = tool.send(self.principal, self.request, approval=self.approval())

        self.assertEqual(first.outcome, "unknown-outcome")
        self.assertTrue(replay.reused)
        self.assertEqual(client.calls, 1)
        self.assertIsNone(first.notification_id)
        self.assertNotIn("recipient", first.model_dump())

    def test_whatsapp_known_transient_outcome_can_be_retried(self) -> None:
        client = OutcomeWhatsAppClient(
            WhatsAppProviderResult(
                outcome="known-transient", provider_code=429, retry_after=10
            ),
            WhatsAppProviderResult(outcome="success", notification_id="wamid.second"),
        )
        tool = WhatsAppNotificationTool(client)

        first = tool.send(self.principal, self.request, approval=self.approval())
        self.assertEqual(first.outcome, "known-transient")
        self.assertEqual(first.retry_after, 10)

        second = tool.send(self.principal, self.request, approval=self.approval())
        self.assertEqual(second.outcome, "success")
        self.assertEqual(second.notification_id, "whatsapp:wamid.second")
        self.assertEqual(client.calls, 2)

    def test_whatsapp_list_sent_returns_successful_items_for_tenant(self) -> None:
        self.tool.send(self.principal, self.request, approval=self.approval())
        sent_items = self.tool.list_sent(self.principal)
        self.assertEqual(len(sent_items), 1)
        self.assertEqual(sent_items[0].notification_id, "whatsapp:wamid.test_1")

        other_principal = Principal.for_test(
            principal_id="user-b",
            tenant_id="tenant-b",
            permission_tier=PermissionTier.P5,
        )
        self.assertEqual(self.tool.list_sent(other_principal), [])


class WhatsAppGraphApiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = WhatsAppGraphApiClient(
            access_token="secret-token-123",
            phone_number_id="100000000000002",
        )

    def test_init_validates_required_fields_and_egress(self) -> None:
        with self.assertRaises(ValueError):
            WhatsAppGraphApiClient(access_token="", phone_number_id="123")
        with self.assertRaises(ValueError):
            WhatsAppGraphApiClient(access_token="tok", phone_number_id="")

        allowlist = EgressAllowlist.from_entries(["api.telegram.org"])
        with self.assertRaises(EgressNotAllowedError):
            WhatsAppGraphApiClient(
                access_token="tok",
                phone_number_id="123",
                egress_allowlist=allowlist,
            )

        valid_allowlist = EgressAllowlist.from_entries(["graph.facebook.com"])
        client = WhatsAppGraphApiClient(
            access_token="tok",
            phone_number_id="123",
            egress_allowlist=valid_allowlist,
        )
        self.assertIsNotNone(client)

    def test_send_message_success_returns_wamid(self) -> None:
        response_json = {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "573001112233", "wa_id": "573001112233"}],
            "messages": [{"id": "wamid.HBgLNTczMDAxMTIyMzM"}],
        }
        response = FakeHttpResponse(json.dumps(response_json).encode("utf-8"))
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            return_value=response,
        ) as urlopen:
            result = self.client.send_message(
                recipient="573001112233", text="Hola mundo"
            )
            self.assertEqual(result.outcome, "success")
            self.assertEqual(result.notification_id, "wamid.HBgLNTczMDAxMTIyMzM")
            self.assertEqual(
                set(result.model_dump()),
                {"outcome", "provider_code", "retry_after", "notification_id"},
            )
        self.assertEqual(urlopen.call_count, 1)

    def test_send_message_http_429_transient_with_retry_after(self) -> None:
        headers = Message()
        headers["Retry-After"] = "15"
        body = TrackingBody(b'{"error":{"message":"Rate limit","code":80007}}')
        error = HTTPError(
            "https://graph.facebook.com/v21.0/100000000000002/messages",
            429,
            "Rate limited",
            headers,
            body,
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.client.send_message(
                recipient="573001112233", text="Hola mundo"
            )
        self.assertEqual(result.outcome, "known-transient")
        self.assertEqual(result.provider_code, 429)
        self.assertEqual(result.retry_after, 15)
        self.assertTrue(body.closed)

    def test_send_message_http_5xx_transient(self) -> None:
        body = TrackingBody(b'{"error":{"message":"Server error","code":500}}')
        error = HTTPError(
            "https://graph.facebook.com/v21.0/100000000000002/messages",
            503,
            "Service Unavailable",
            Message(),
            body,
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.client.send_message(
                recipient="573001112233", text="Hola mundo"
            )
        self.assertEqual(result.outcome, "known-transient")
        self.assertEqual(result.provider_code, 503)

    def test_send_message_http_4xx_permanent(self) -> None:
        body = TrackingBody(b'{"error":{"message":"Invalid OAuth token","code":190}}')
        error = HTTPError(
            "https://graph.facebook.com/v21.0/100000000000002/messages",
            401,
            "Unauthorized",
            Message(),
            body,
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.client.send_message(
                recipient="573001112233", text="Hola mundo"
            )
        self.assertEqual(result.outcome, "permanent")
        self.assertEqual(result.provider_code, 401)

    def test_send_message_network_errors_are_unknown_outcome(self) -> None:
        for failure in (
            TimeoutError("private network timeout"),
            ConnectionResetError("connection reset"),
            IncompleteRead(b"partial"),
            URLError("dns resolution failure"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with patch(
                    "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
                    side_effect=failure,
                ):
                    result = self.client.send_message(
                        recipient="573001112233", text="Hola mundo"
                    )
                self.assertEqual(result.outcome, "unknown-outcome")
                self.assertIsNone(result.notification_id)

    def test_send_message_malformed_json_is_unknown_outcome(self) -> None:
        for bad_body in (
            b"not-json",
            b"{}",
            b'{"messages":[]}',
            b'{"messages":[{"id":""}]}',
        ):
            with self.subTest(bad_body=bad_body):
                with patch(
                    "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
                    return_value=FakeHttpResponse(bad_body),
                ):
                    result = self.client.send_message(
                        recipient="573001112233", text="Hola mundo"
                    )
                self.assertEqual(result.outcome, "unknown-outcome")


class ChannelNotificationRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        from personal_assistant.adapters.outbound.notifications.telegram import (
            TelegramNotificationTool,
        )
        from tests.test_telegram_notifications import FakeTelegramClient

        self.telegram_client = FakeTelegramClient()
        self.telegram_tool = TelegramNotificationTool(self.telegram_client)
        self.whatsapp_client = FakeWhatsAppClient()
        self.whatsapp_tool = WhatsAppNotificationTool(self.whatsapp_client)

        self.router = ChannelNotificationRouter(
            {
                "telegram": self.telegram_tool,
                "whatsapp": self.whatsapp_tool,
            }
        )
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-1",
            permission_tier=PermissionTier.P5,
        )

    def test_router_delegates_to_correct_channel(self) -> None:
        req_tg = NotificationRequest(
            channel="telegram",
            recipient="chat-1",
            body="Hola Telegram",
            idempotency_key="msg-tg-1",
        )
        grant_tg = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=req_tg.idempotency_key,
            tier=PermissionTier.P5,
        )
        res_tg = self.router.send(self.principal, req_tg, approval=grant_tg)
        self.assertEqual(res_tg.outcome, "success")
        self.assertEqual(res_tg.channel, "telegram")
        self.assertEqual(len(self.telegram_client.sent), 1)
        self.assertEqual(len(self.whatsapp_client.sent), 0)

        req_wa = NotificationRequest(
            channel="whatsapp",
            recipient="573001112233",
            body="Hola WhatsApp",
            idempotency_key="msg-wa-1",
        )
        grant_wa = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=req_wa.idempotency_key,
            tier=PermissionTier.P5,
        )
        res_wa = self.router.send(self.principal, req_wa, approval=grant_wa)
        self.assertEqual(res_wa.outcome, "success")
        self.assertEqual(res_wa.channel, "whatsapp")
        self.assertEqual(len(self.whatsapp_client.sent), 1)

    def test_router_unknown_channel_fails_permanently_without_error_leak(self) -> None:
        req_unknown = NotificationRequest(
            channel="sms",
            recipient="+573001112233",
            body="Secret sms text",
            idempotency_key="msg-sms-1",
        )
        grant = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=req_unknown.idempotency_key,
            tier=PermissionTier.P5,
        )
        res = self.router.send(self.principal, req_unknown, approval=grant)
        self.assertEqual(res.outcome, "permanent")
        self.assertEqual(res.channel, "sms")
        self.assertIsNone(res.notification_id)
        # Ensure no private recipient or body is in model dump
        self.assertNotIn("Secret sms text", str(res.model_dump()))
        self.assertNotIn("+573001112233", str(res.model_dump()))

    def test_router_list_sent_consolidates_channels(self) -> None:
        req_wa = NotificationRequest(
            channel="whatsapp",
            recipient="573001112233",
            body="Hola WhatsApp",
            idempotency_key="msg-wa-list-1",
        )
        grant_wa = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=req_wa.idempotency_key,
            tier=PermissionTier.P5,
        )
        self.router.send(self.principal, req_wa, approval=grant_wa)
        items = self.router.list_sent(self.principal)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].channel, "whatsapp")


class WhatsAppE2EGherkinScenarioTests(unittest.TestCase):
    """End-to-end tests mapping 1:1 to features/whatsapp_outbound_delivery.feature."""

    def test_scenario_1_assistant_reply_delivered_back_over_whatsapp_outline(
        self,
    ) -> None:
        examples = [
            ("573001112233", "personal", "remind me to call Ana at 5 pm"),
            ("573004445566", "personal", "what do I have for today"),
        ]
        for wa_number, tenant_id, text in examples:
            with self.subTest(wa_number=wa_number, text=text):
                settings = AppSettings(
                    tenant_id=tenant_id,
                    timezone="America/Bogota",
                    reply_locale="es",
                    persistence_backend="memory",
                    whatsapp=WhatsAppSettings(
                        enabled=True,
                        app_secret="test-secret",
                        verify_token="test-token",
                        allowed_user_ids=frozenset({"573001112233", "573004445566"}),
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
                    wa_number, text, message_id=f"wamid.{wa_number}"
                )
                body = json.dumps(payload).encode("utf-8")
                sig = _sign_payload(body, settings.whatsapp.app_secret)
                response = test_client.post(
                    "/webhooks/whatsapp",
                    content=body,
                    headers={
                        "x-hub-signature-256": sig,
                        "content-type": "application/json",
                    },
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertTrue(data["sent"])
                self.assertIsNotNone(data["reply"])
                self.assertEqual(len(client.sent), 1)
                self.assertEqual(client.sent[0]["recipient"], wa_number)

    def test_scenario_2_due_reminder_created_from_whatsapp_delivered_to_whatsapp(
        self,
    ) -> None:
        now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        actor = Principal.for_test(
            principal_id="573001112233",
            tenant_id="personal",
            permission_tier=PermissionTier.P5,
        )
        client = FakeWhatsAppClient()
        tool = WhatsAppNotificationTool(client)
        router = ChannelNotificationRouter({"whatsapp": tool})
        clock = FakeClock(now)

        container = build_container(
            persistence_backend="memory",
            notifications=router,
            approve_reminder_notifications=True,
        )
        container.reminder_worker.approval_policy = RuntimeNotificationApprovalPolicy(
            approve_notifications=True, approval_ttl=None
        )
        container.reminder_notifications.clock = clock
        container.reminder_worker.clock = clock

        job = container.scheduler.schedule_before_event(
            actor,
            calendar_event_id="cal-wa-1",
            starts_at=now,
            channel="whatsapp",
            recipient="573001112233",
            body="Recordatorio cita medica",
            minutes_before=0,
            idempotency_key="notify-wa-1",
            timezone="America/Bogota",
            source_event_id="source-wa-1",
            payload_fingerprint="a" * 64,
        )
        event = CloudEvent(
            type="notification.requested",
            source="test",
            subject=job.reminder_id,
            tenant_id=actor.tenant_id,
            data={
                "channel": "whatsapp",
                "recipient": "573001112233",
                "body": "Recordatorio cita medica",
            },
        )
        outbox_entry = container.outbox.add(
            actor,
            event,
            idempotency_key="outbox-wa-1",
            next_attempt_at=now,
        )

        tick = container.reminder_worker.run_once(actor)
        self.assertIn(outbox_entry.id, tick.claimed_message_ids)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.sent[0]["recipient"], "573001112233")

        messages = container.outbox.list_for_tenant(actor)
        target = next(m for m in messages if m.id == outbox_entry.id)
        self.assertEqual(target.dispatch_status, OutboxStatus.published)

    def test_scenario_3_transient_provider_error_keeps_delivery_pending_for_retry(
        self,
    ) -> None:
        now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        actor = Principal.for_test(
            principal_id="573001112233",
            tenant_id="personal",
            permission_tier=PermissionTier.P5,
        )
        client = OutcomeWhatsAppClient(
            WhatsAppProviderResult(outcome="known-transient", provider_code=503)
        )
        tool = WhatsAppNotificationTool(client)
        router = ChannelNotificationRouter({"whatsapp": tool})
        clock = FakeClock(now)

        container = build_container(
            persistence_backend="memory",
            notifications=router,
            approve_reminder_notifications=True,
        )
        container.reminder_worker.approval_policy = RuntimeNotificationApprovalPolicy(
            approve_notifications=True, approval_ttl=None
        )
        container.reminder_notifications.clock = clock
        container.reminder_worker.clock = clock

        job = container.scheduler.schedule_before_event(
            actor,
            calendar_event_id="cal-wa-2",
            starts_at=now,
            channel="whatsapp",
            recipient="573001112233",
            body="Recordatorio pago",
            minutes_before=0,
            idempotency_key="notify-wa-2",
            timezone="America/Bogota",
            source_event_id="source-wa-2",
            payload_fingerprint="b" * 64,
        )
        event = CloudEvent(
            type="notification.requested",
            source="test",
            subject=job.reminder_id,
            tenant_id=actor.tenant_id,
            data={
                "channel": "whatsapp",
                "recipient": "573001112233",
                "body": "Recordatorio pago",
            },
        )
        outbox_entry = container.outbox.add(
            actor,
            event,
            idempotency_key="outbox-wa-2",
            next_attempt_at=now,
        )

        container.reminder_worker.run_once(actor)
        self.assertEqual(client.calls, 1)

        messages = container.outbox.list_for_tenant(actor)
        target = next(m for m in messages if m.id == outbox_entry.id)
        self.assertEqual(target.dispatch_status, OutboxStatus.pending)
        self.assertGreater(target.attempts, 0)

    def test_scenario_4_ambiguous_provider_outcome_reconciled_as_uncertain(
        self,
    ) -> None:
        now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        actor = Principal.for_test(
            principal_id="573001112233",
            tenant_id="personal",
            permission_tier=PermissionTier.P5,
        )
        client = OutcomeWhatsAppClient(
            WhatsAppProviderResult(outcome="unknown-outcome")
        )
        tool = WhatsAppNotificationTool(client)
        router = ChannelNotificationRouter({"whatsapp": tool})
        clock = FakeClock(now)

        container = build_container(
            persistence_backend="memory",
            notifications=router,
            approve_reminder_notifications=True,
        )
        container.reminder_worker.approval_policy = RuntimeNotificationApprovalPolicy(
            approve_notifications=True, approval_ttl=None
        )
        container.reminder_notifications.clock = clock
        container.reminder_worker.clock = clock

        job = container.scheduler.schedule_before_event(
            actor,
            calendar_event_id="cal-wa-3",
            starts_at=now,
            channel="whatsapp",
            recipient="573001112233",
            body="Confidential meeting reminder",
            minutes_before=0,
            idempotency_key="notify-wa-3",
            timezone="America/Bogota",
            source_event_id="source-wa-3",
            payload_fingerprint="c" * 64,
        )
        event = CloudEvent(
            type="notification.requested",
            source="test",
            subject=job.reminder_id,
            tenant_id=actor.tenant_id,
            data={
                "channel": "whatsapp",
                "recipient": "573001112233",
                "body": "Confidential meeting reminder",
            },
        )
        outbox_entry = container.outbox.add(
            actor,
            event,
            idempotency_key="outbox-wa-3",
            next_attempt_at=now,
        )

        container.reminder_worker.run_once(actor)
        self.assertEqual(client.calls, 1)

        messages = container.outbox.list_for_tenant(actor)
        target = next(m for m in messages if m.id == outbox_entry.id)
        self.assertEqual(target.dispatch_status, OutboxStatus.uncertain)
        # Verify no confidential content leak
        self.assertNotIn("Confidential meeting reminder", str(target.last_error))

    def test_scenario_5_without_access_token_reply_is_skipped_gracefully(self) -> None:
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
                access_token=None,
                phone_number_id="100000000000002",
            ),
        )
        container = build_container(settings=settings)
        app = create_app(container=container, settings=settings)
        test_client = TestClient(app)

        payload = _make_whatsapp_payload(
            "573001112233",
            "remind me to buy groceries",
            message_id="wamid.notoken001",
        )
        body = json.dumps(payload).encode("utf-8")
        sig = _sign_payload(body, settings.whatsapp.app_secret)
        response = test_client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"x-hub-signature-256": sig, "content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["sent"])
        self.assertIsNotNone(data["reply"])


class WhatsAppRuntimeWiringTests(unittest.TestCase):
    def test_build_runtime_container_with_whatsapp(self) -> None:
        settings = AppSettings(
            tenant_id="personal",
            timezone="America/Bogota",
            persistence_backend="memory",
            whatsapp=WhatsAppSettings(
                enabled=True,
                access_token="test-token",
                phone_number_id="100000000000002",
            ),
        )
        container = build_runtime_container(settings)
        self.assertIsInstance(container.notifications, ChannelNotificationRouter)
        self.assertIn("whatsapp", container.notifications._channels)
        self.assertNotIn("telegram", container.notifications._channels)

    def test_build_runtime_container_with_both_channels(self) -> None:
        settings = AppSettings(
            tenant_id="personal",
            timezone="America/Bogota",
            persistence_backend="memory",
            telegram_bot_token="test-tg-token",
            whatsapp=WhatsAppSettings(
                enabled=True,
                access_token="test-wa-token",
                phone_number_id="100000000000002",
            ),
        )
        container = build_runtime_container(settings)
        self.assertIsInstance(container.notifications, ChannelNotificationRouter)
        self.assertIn("whatsapp", container.notifications._channels)
        self.assertIn("telegram", container.notifications._channels)

    def test_build_runtime_container_with_neither_channel(self) -> None:
        settings = AppSettings(
            tenant_id="personal",
            timezone="America/Bogota",
            persistence_backend="memory",
        )
        container = build_runtime_container(settings)
        # Without provider credentials, notifications router is not built (falls back to local tool)
        self.assertNotIsInstance(container.notifications, ChannelNotificationRouter)

    @patch("personal_assistant.infrastructure.config_settings.AppSettings.from_env")
    def test_worker_runtime_with_whatsapp_token(self, mock_from_env: Any) -> None:
        mock_from_env.return_value = AppSettings(
            tenant_id="personal",
            timezone="America/Bogota",
            persistence_backend="postgres",
            database_url="postgresql://test:test@localhost:5432/test",
            whatsapp=WhatsAppSettings(
                enabled=True,
                access_token="test-token",
                phone_number_id="100000000000002",
            ),
        )
        with patch(
            "personal_assistant.infrastructure.bootstrap.build_container"
        ) as mock_build:
            _container, principal, _settings = _runtime(require_provider=True)
            self.assertEqual(principal.principal_id, "reminder-worker")
            self.assertEqual(mock_build.call_count, 1)
            notifications_arg = mock_build.call_args.kwargs.get("notifications")
            self.assertIsInstance(notifications_arg, ChannelNotificationRouter)

    def test_whatsapp_graph_client_get_media_url(self) -> None:
        allowlist = EgressAllowlist.from_entries({"graph.facebook.com"})
        client = WhatsAppGraphApiClient(
            access_token="wa-token",
            phone_number_id="12345",
            egress_allowlist=allowlist,
        )
        fake_response = io.BytesIO(
            json.dumps({"url": "https://lookaside.fbsbx.com/wa_media_1"}).encode(
                "utf-8"
            )
        )
        with patch(
            "urllib.request.urlopen", return_value=fake_response
        ) as mock_urlopen:
            url = client.get_media_url("media-id-123")
            self.assertEqual(url, "https://lookaside.fbsbx.com/wa_media_1")
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(
                req.full_url, "https://graph.facebook.com/v21.0/media-id-123"
            )
            self.assertEqual(req.headers.get("Authorization"), "Bearer wa-token")

    def test_whatsapp_graph_client_download_media_follows_allowlisted_redirects(
        self,
    ) -> None:
        allowlist = EgressAllowlist.from_entries(
            {"graph.facebook.com", "lookaside.fbsbx.com"}
        )
        client = WhatsAppGraphApiClient(
            access_token="wa-token",
            phone_number_id="12345",
            egress_allowlist=allowlist,
        )

        class FakeOpener:
            def __init__(self) -> None:
                self.calls = 0

            def open(self, req: Any, timeout: float = 10.0) -> Any:
                self.calls += 1
                if self.calls == 1:
                    headers = Message()
                    headers["Location"] = (
                        "https://lookaside.fbsbx.com/download/file.ogg"
                    )
                    raise HTTPError(
                        req.full_url, 302, "Found", headers, io.BytesIO(b"")
                    )
                return io.BytesIO(b"AUDIO_BYTES_OK")

        fake_opener = FakeOpener()
        with patch("urllib.request.build_opener", return_value=fake_opener):
            data = client.download_media("https://graph.facebook.com/v21.0/media-dl")
            self.assertEqual(data, b"AUDIO_BYTES_OK")
            self.assertEqual(fake_opener.calls, 2)

    def test_whatsapp_graph_client_download_media_blocks_unauthorized_redirect(
        self,
    ) -> None:
        allowlist = EgressAllowlist.from_entries({"graph.facebook.com"})
        client = WhatsAppGraphApiClient(
            access_token="wa-token",
            phone_number_id="12345",
            egress_allowlist=allowlist,
        )

        class EvilRedirectOpener:
            def open(self, req: Any, timeout: float = 10.0) -> Any:
                headers = Message()
                headers["Location"] = "https://evil-attacker.com/leak"
                raise HTTPError(req.full_url, 302, "Found", headers, io.BytesIO(b""))

        with (
            patch("urllib.request.build_opener", return_value=EvilRedirectOpener()),
            self.assertRaises(EgressNotAllowedError),
        ):
            client.download_media("https://graph.facebook.com/v21.0/media-dl")

    def test_no_redirect_handler_returns_none(self) -> None:
        from personal_assistant.adapters.outbound.notifications.whatsapp_client import (
            _NoRedirectHandler,
        )

        handler = _NoRedirectHandler()
        req = urllib_request.Request("https://graph.facebook.com/v21.0/media-dl")
        self.assertIsNone(
            handler.redirect_request(
                req, None, 302, "Found", Message(), "https://lookaside.fbsbx.com"
            )
        )

    def test_whatsapp_graph_client_get_media_url_validation_and_errors(
        self,
    ) -> None:
        client = WhatsAppGraphApiClient(
            access_token="wa-token",
            phone_number_id="12345",
        )

        with self.assertRaises(ValueError):
            client.get_media_url("")
        with self.assertRaises(ValueError):
            client.get_media_url("   ")

        # Invalid response body without "url"
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            return_value=io.BytesIO(b'{"id": "123"}'),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.get_media_url("media-123")
            self.assertIn("invalid response", str(ctx.exception))

        # HTTP error 404
        error = HTTPError(
            "https://graph.facebook.com/v21.0/media-123",
            404,
            "Not Found",
            Message(),
            io.BytesIO(b'{"error": "not found"}'),
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            side_effect=error,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.get_media_url("media-123")
            self.assertIn("failed with status 404", str(ctx.exception))

        # URLError / TimeoutError
        with patch(
            "personal_assistant.adapters.outbound.notifications.whatsapp_client.urllib_request.urlopen",
            side_effect=URLError("Connection refused"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.get_media_url("media-123")
            self.assertIn("network error", str(ctx.exception))

    def test_whatsapp_graph_client_download_media_errors(self) -> None:
        allowlist = EgressAllowlist.from_entries(
            {"graph.facebook.com", "lookaside.fbsbx.com"}
        )
        client = WhatsAppGraphApiClient(
            access_token="wa-token",
            phone_number_id="12345",
            egress_allowlist=allowlist,
        )

        # Redirect missing Location header
        class MissingLocationOpener:
            def open(self, req: Any, timeout: float = 10.0) -> Any:
                raise HTTPError(req.full_url, 302, "Found", Message(), io.BytesIO(b""))

        with patch("urllib.request.build_opener", return_value=MissingLocationOpener()):
            with self.assertRaises(RuntimeError) as ctx:
                client.download_media("https://graph.facebook.com/v21.0/media-dl")
            self.assertIn("Redirect without Location header", str(ctx.exception))

        # Too many redirects (> 5)
        class LoopRedirectOpener:
            def open(self, req: Any, timeout: float = 10.0) -> Any:
                headers = Message()
                headers["Location"] = "https://lookaside.fbsbx.com/loop"
                raise HTTPError(req.full_url, 302, "Found", headers, io.BytesIO(b""))

        with patch("urllib.request.build_opener", return_value=LoopRedirectOpener()):
            with self.assertRaises(RuntimeError) as ctx:
                client.download_media("https://graph.facebook.com/v21.0/media-dl")
            self.assertIn("Too many redirects", str(ctx.exception))

        # Non-3xx HTTPError
        class Http404Opener:
            def open(self, req: Any, timeout: float = 10.0) -> Any:
                raise HTTPError(
                    req.full_url,
                    404,
                    "Not Found",
                    Message(),
                    io.BytesIO(b"not found"),
                )

        with patch("urllib.request.build_opener", return_value=Http404Opener()):
            with self.assertRaises(RuntimeError) as ctx:
                client.download_media("https://graph.facebook.com/v21.0/media-dl")
            self.assertIn("failed with status 404", str(ctx.exception))

        # URLError
        class NetworkErrorOpener:
            def open(self, req: Any, timeout: float = 10.0) -> Any:
                raise URLError("Unreachable")

        with patch("urllib.request.build_opener", return_value=NetworkErrorOpener()):
            with self.assertRaises(RuntimeError) as ctx:
                client.download_media("https://graph.facebook.com/v21.0/media-dl")
            self.assertIn("network error", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
