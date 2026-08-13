from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from personal_assistant.application.dto.commands import (
    PendingApproval,
    PendingApprovalStatus,
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


def _approval(
    approval_id: str,
    *,
    status: PendingApprovalStatus = PendingApprovalStatus.pending,
    text: str = "clase el martes a las 11",
) -> PendingApproval:
    return PendingApproval(
        approval_id=approval_id,
        tenant_id="tenant-a",
        principal_id="user-1",
        action="calendar.create_event",
        resource=f"{approval_id}:calendar",
        tier="P3",
        workflow_kind="reminder.create",
        message_id="42",
        source_event_id="evt-42",
        conversation_id="chat-1",
        channel="telegram",
        recipient="chat-1",
        request_text=text,
        request_now=datetime(2026, 6, 20, 12, tzinfo=UTC),
        idempotency_key=f"key-{approval_id}",
        payload_fingerprint="a" * 64,
        status=status,
    )


class AdminApprovalActionRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = build_container()
        self.principal = Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.dashboard = AdminDashboard(self.container)

    def test_pending_approval_row_renders_approve_and_reject_actions(self) -> None:
        self.container.approvals.create(self.principal, _approval("ap-001"))

        html = self.dashboard.render_html(self.principal)

        self.assertIn('data-approval-id="ap-001"', html)
        self.assertIn(
            '<button type="button" data-approval-action="approve" '
            'data-approval-id="ap-001"',
            html,
        )
        self.assertIn(
            '<button type="button" data-approval-action="reject" '
            'data-approval-id="ap-001"',
            html,
        )

    def test_non_pending_approval_rows_render_no_actions(self) -> None:
        self.container.approvals.create(
            self.principal,
            _approval("ap-002", status=PendingApprovalStatus.approved),
        )
        self.container.approvals.create(
            self.principal,
            _approval("ap-003", status=PendingApprovalStatus.cancelled),
        )

        html = self.dashboard.render_html(self.principal)

        self.assertIn('data-label="Approval Id">ap-002</td>', html)
        self.assertIn('data-label="Approval Id">ap-003</td>', html)
        self.assertIn("approved", html)
        self.assertIn("rejected", html)
        self.assertNotIn(
            '<button type="button" data-approval-action="approve" '
            'data-approval-id="ap-002"',
            html,
        )
        self.assertNotIn(
            '<button type="button" data-approval-action="reject" '
            'data-approval-id="ap-002"',
            html,
        )
        self.assertNotIn(
            '<button type="button" data-approval-action="approve" '
            'data-approval-id="ap-003"',
            html,
        )
        self.assertNotIn(
            '<button type="button" data-approval-action="reject" '
            'data-approval-id="ap-003"',
            html,
        )

    def test_page_renders_single_token_input_and_action_script(self) -> None:
        html = self.dashboard.render_html(self.principal)

        self.assertEqual(html.count('id="admin-token"'), 1)
        self.assertIn('type="password"', html)
        self.assertIn('autocomplete="off"', html)
        self.assertIn("window.confirm", html)
        self.assertIn("Authorization", html)
        self.assertIn("Bearer ", html)
        self.assertIn("/v1/runtime/approvals/", html)
        self.assertIn("textContent", html)

    def test_token_is_never_persisted_or_leaked_to_urls(self) -> None:
        html = self.dashboard.render_html(self.principal)

        for marker in ("localStorage", "sessionStorage", "document.cookie"):
            self.assertNotIn(marker, html)

    def test_conflict_feedback_uses_fresh_server_status(self) -> None:
        html = self.dashboard.render_html(self.principal)

        self.assertIn('response.status === 404', html)
        self.assertIn("approval not found", html)
        self.assertIn('response.status === 409', html)
        self.assertIn('fetch("/v1/runtime/approvals"', html)
        self.assertIn("approval was already ${currentStatus}", html)

    def test_approval_text_is_escaped_in_action_markup(self) -> None:
        self.container.approvals.create(
            self.principal,
            _approval("ap-004", text='<script>alert("x")</script>'),
        )

        html = self.dashboard.render_html(self.principal)

        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)


class AdminApprovalActionHttpTests(unittest.TestCase):
    admin_token = "admin-secret"
    headers: ClassVar[dict[str, str]] = {"Authorization": f"Bearer {admin_token}"}

    def setUp(self) -> None:
        self.container = build_container()
        self.client = TestClient(
            create_app(
                self.container,
                settings=AppSettings(
                    tenant_id="tenant-a",
                    admin_token=self.admin_token,
                    local_auth_principal_id="user-1",
                    local_auth_permission_tier=PermissionTier.P5,
                ),
            ),
            client=("127.0.0.1", 50000),
        )

    def request_pending_approval(self) -> str:
        response = self.client.post(
            "/v1/runtime/reminders",
            json={
                "message_id": "42",
                "source_event_id": "api-request-42",
                "conversation_id": "chat-1",
                "text": "recuérdame clase el martes a las 17",
                "channel": "telegram",
                "recipient": "chat-1",
                "now": "2026-06-20T12:00:00+00:00",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["approval"]["approval_id"]

    def principal(self) -> Principal:
        return Principal.for_test(
            principal_id="user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )

    def test_dashboard_offers_actions_for_pending_approval(self) -> None:
        approval_id = self.request_pending_approval()

        page = self.client.get("/admin", headers=self.headers)

        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn(f'data-approval-id="{approval_id}"', page.text)
        self.assertIn(
            f'<button type="button" data-approval-action="approve" '
            f'data-approval-id="{approval_id}"',
            page.text,
        )
        self.assertIn(
            f'<button type="button" data-approval-action="reject" '
            f'data-approval-id="{approval_id}"',
            page.text,
        )

    def test_approve_flow_shows_approved_and_removes_actions(self) -> None:
        approval_id = self.request_pending_approval()

        decided = self.client.post(
            f"/v1/runtime/approvals/{approval_id}/approve",
            headers=self.headers,
        )
        page = self.client.get("/admin", headers=self.headers)

        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["status"], "approved")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn(f'data-label="Approval Id">{approval_id}</td>', page.text)
        self.assertIn(
            'data-label="Status" data-approval-status>approved</td>', page.text
        )
        self.assertNotIn(
            f'data-approval-action="approve" data-approval-id="{approval_id}"',
            page.text,
        )
        self.assertNotIn(
            f'data-approval-action="reject" data-approval-id="{approval_id}"',
            page.text,
        )

    def test_reject_flow_shows_rejected_and_removes_actions(self) -> None:
        approval_id = self.request_pending_approval()

        decided = self.client.post(
            f"/v1/runtime/approvals/{approval_id}/reject",
            headers=self.headers,
        )
        page = self.client.get("/admin", headers=self.headers)

        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["status"], "rejected")
        self.assertNotIn(
            f'data-approval-action="approve" data-approval-id="{approval_id}"',
            page.text,
        )
        self.assertNotIn(
            f'data-approval-action="reject" data-approval-id="{approval_id}"',
            page.text,
        )

    def test_approve_on_rejected_surfaces_conflict_without_side_effects(self) -> None:
        approval_id = self.request_pending_approval()
        rejected = self.client.post(
            f"/v1/runtime/approvals/{approval_id}/reject",
            headers=self.headers,
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)

        conflict = self.client.post(
            f"/v1/runtime/approvals/{approval_id}/approve",
            headers=self.headers,
        )

        # The API sanitizes the conflict message ("request conflict"); the
        # dashboard JS derives the specific reason from the fresh status in
        # GET /v1/runtime/approvals, which is what we assert here.
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "conflict")
        self.assertEqual(conflict.json()["error"]["message"], "request conflict")
        approvals = self.client.get("/v1/runtime/approvals", headers=self.headers)
        self.assertEqual(approvals.status_code, 200, approvals.text)
        statuses = {
            item["approval_id"]: item["status"] for item in approvals.json()
        }
        self.assertEqual(statuses[approval_id], "rejected")
        self.assertEqual(self.container.calendar.list_events(self.principal()), [])
        self.assertEqual(self.container.scheduler.list_for_tenant(self.principal()), [])

    def test_reject_on_approved_surfaces_conflict(self) -> None:
        approval_id = self.request_pending_approval()
        approved = self.client.post(
            f"/v1/runtime/approvals/{approval_id}/approve",
            headers=self.headers,
        )
        self.assertEqual(approved.status_code, 200, approved.text)

        conflict = self.client.post(
            f"/v1/runtime/approvals/{approval_id}/reject",
            headers=self.headers,
        )

        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "conflict")
        self.assertEqual(conflict.json()["error"]["message"], "request conflict")
        approvals = self.client.get("/v1/runtime/approvals", headers=self.headers)
        self.assertEqual(approvals.status_code, 200, approvals.text)
        statuses = {
            item["approval_id"]: item["status"] for item in approvals.json()
        }
        self.assertEqual(statuses[approval_id], "approved")

    def test_unknown_approval_returns_not_found_detail(self) -> None:
        missing = self.client.post(
            "/v1/runtime/approvals/apr_missing/approve",
            headers=self.headers,
        )

        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["error"]["code"], "not_found")
        self.assertEqual(missing.json()["error"]["message"], "resource not found")


if __name__ == "__main__":
    unittest.main()
