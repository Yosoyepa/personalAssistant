from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from personal_assistant.application.dto.delivery import (
    DeliveryError,
    DeliveryErrorCategory,
    DeliveryErrorCode,
)
from personal_assistant.application.dto.events import (
    CloudEvent,
    OutboxMessage,
    OutboxStatus,
)
from personal_assistant.application.use_cases.reminder_notifications import (
    MAX_DELIVERY_ATTEMPTS,
    REMINDER_NOTIFICATION_EVENT_TYPE,
)
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import PermissionTier
from personal_assistant.infrastructure.admin import AdminDashboard
from personal_assistant.infrastructure.bootstrap import build_container

try:
    from fastapi.testclient import TestClient

    from personal_assistant.infrastructure.config import AppSettings
    from personal_assistant.infrastructure.http import create_app
except ImportError:
    pytest.skip(
        "FastAPI optional dependency is not installed", allow_module_level=True
    )


def _outbox_message(
    message_id: str,
    *,
    tenant_id: str = "personal",
    status: OutboxStatus = OutboxStatus.uncertain,
    attempts: int = 1,
    event_type: str = REMINDER_NOTIFICATION_EVENT_TYPE,
) -> OutboxMessage:
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)
    event = CloudEvent(
        id=f"evt-{message_id}",
        type=event_type,
        source="reminders",
        subject=f"rem-{message_id}",
        tenant_id=tenant_id,
        data={"recipient": "chat-1", "channel": "telegram", "body": "reminder text"},
        time=now,
    )
    return OutboxMessage(
        id=message_id,
        tenant_id=tenant_id,
        event=event,
        idempotency_key=f"idemp-{message_id}",
        dispatch_status=status,
        attempts=attempts,
        created_at=now,
        sending_at=now if status in {OutboxStatus.uncertain, OutboxStatus.failed, OutboxStatus.sending} else None,
        published_at=now if status == OutboxStatus.published else None,
        last_error=DeliveryError(
            category=DeliveryErrorCategory.network,
            code=DeliveryErrorCode.provider_unavailable,
            occurred_at=now,
        )
        if status in {OutboxStatus.uncertain, OutboxStatus.failed}
        else None,
    )


def _insert_outbox(
    container: build_container,
    principal: Principal,
    message: OutboxMessage,
) -> None:
    key = (principal.tenant_id, message.idempotency_key)
    container.outbox._messages_by_key[key] = message
    container.outbox._key_by_message_id[(principal.tenant_id, message.id)] = (
        message.idempotency_key
    )
    container.outbox._key_by_event_id[(principal.tenant_id, message.event.id)] = (
        message.idempotency_key
    )


class AdminUncertainResolutionRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = build_container()
        self.principal = Principal.for_test(
            principal_id="operator-1",
            tenant_id="personal",
            permission_tier=PermissionTier.P5,
        )
        self.dashboard = AdminDashboard(self.container)

    def test_uncertain_row_renders_resolve_delivered_and_retry_buttons(self) -> None:
        _insert_outbox(
            self.container, self.principal, _outbox_message("msg-001", status=OutboxStatus.uncertain)
        )

        html = self.dashboard.render_html(self.principal)

        self.assertIn('data-label="Id">msg-001</td>', html)
        self.assertIn(
            '<button type="button" data-outbox-action="delivered" data-outbox-id="msg-001">Resolve Delivered</button>',
            html,
        )
        self.assertIn(
            '<button type="button" data-outbox-action="retry" data-outbox-id="msg-001">Resolve Retry</button>',
            html,
        )

    def test_non_uncertain_rows_render_no_actions(self) -> None:
        _insert_outbox(
            self.container, self.principal, _outbox_message("msg-003", status=OutboxStatus.published)
        )
        _insert_outbox(
            self.container, self.principal, _outbox_message("msg-pending", status=OutboxStatus.pending)
        )

        html = self.dashboard.render_html(self.principal)

        self.assertIn('data-label="Id">msg-003</td>', html)
        self.assertIn('data-label="Id">msg-pending</td>', html)
        self.assertNotIn('data-outbox-id="msg-003"', html)
        self.assertNotIn('data-outbox-id="msg-pending"', html)

    def test_page_renders_token_input_and_outbox_script(self) -> None:
        html = self.dashboard.render_html(self.principal)

        self.assertIn('id="outbox-action-status"', html)
        self.assertIn("/v1/runtime/outbox/", html)
        self.assertIn("data-outbox-action", html)
        self.assertIn("window.confirm", html)


class AdminUncertainResolutionHttpTests(unittest.TestCase):
    admin_token = "admin-secret"
    headers: ClassVar[dict[str, str]] = {"Authorization": f"Bearer {admin_token}"}

    def setUp(self) -> None:
        self.container = build_container()
        self.principal = Principal.for_test(
            principal_id="operator-1",
            tenant_id="personal",
            permission_tier=PermissionTier.P5,
        )
        self.client = TestClient(
            create_app(
                self.container,
                settings=AppSettings(
                    tenant_id="personal",
                    admin_token=self.admin_token,
                    local_auth_principal_id="operator-1",
                    local_auth_permission_tier=PermissionTier.P5,
                ),
            ),
            client=("127.0.0.1", 50000),
        )

    def test_operator_resolves_delivered_from_outbox_section(self) -> None:
        _insert_outbox(
            self.container, self.principal, _outbox_message("msg-001", status=OutboxStatus.uncertain)
        )

        response = self.client.post(
            "/v1/runtime/outbox/msg-001/resolve",
            json={"resolution": "delivered"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "published")
        self.assertEqual(response.json()["message_id"], "msg-001")

        # Verify admin dashboard presentation
        page = self.client.get("/admin", headers=self.headers)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('data-label="Id">msg-001</td>', page.text)
        self.assertIn(
            'data-label="Status" data-outbox-status>published</td>', page.text
        )
        self.assertNotIn('data-outbox-id="msg-001"', page.text)

    def test_operator_resolves_retry_from_outbox_section(self) -> None:
        _insert_outbox(
            self.container, self.principal, _outbox_message("msg-002", status=OutboxStatus.uncertain)
        )

        response = self.client.post(
            "/v1/runtime/outbox/msg-002/resolve",
            json={"resolution": "retry"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["message_id"], "msg-002")

        # Verify admin dashboard presentation
        page = self.client.get("/admin", headers=self.headers)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('data-label="Id">msg-002</td>', page.text)
        self.assertIn('data-label="Status" data-outbox-status>pending</td>', page.text)
        self.assertNotIn('data-outbox-id="msg-002"', page.text)

    def test_retry_beyond_attempt_limit_is_rejected_without_side_effects(self) -> None:
        _insert_outbox(
            self.container,
            self.principal,
            _outbox_message(
                "msg-004",
                status=OutboxStatus.uncertain,
                attempts=MAX_DELIVERY_ATTEMPTS,
            ),
        )

        response = self.client.post(
            "/v1/runtime/outbox/msg-004/resolve",
            json={"resolution": "retry"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "validation_failed")
        self.assertEqual(
            response.json()["error"]["message"], "request validation failed"
        )

        # Message stays uncertain
        messages = self.container.outbox.list_for_tenant(self.principal)
        target = next(m for m in messages if m.id == "msg-004")
        self.assertEqual(target.dispatch_status, OutboxStatus.uncertain)

    def test_resolve_on_published_message_surfaces_conflict(self) -> None:
        _insert_outbox(
            self.container, self.principal, _outbox_message("msg-005", status=OutboxStatus.published)
        )

        response = self.client.post(
            "/v1/runtime/outbox/msg-005/resolve",
            json={"resolution": "delivered"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "conflict")

    def test_resolve_on_unknown_message_returns_not_found(self) -> None:
        response = self.client.post(
            "/v1/runtime/outbox/msg-missing/resolve",
            json={"resolution": "delivered"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_resolve_requires_authentication(self) -> None:
        response = self.client.post(
            "/v1/runtime/outbox/msg-001/resolve",
            json={"resolution": "delivered"},
        )

        self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()
