from __future__ import annotations

import io
import unittest
from email.message import Message
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from personal_assistant.adapters.outbound.notifications.telegram import (
    TelegramBotApiClient,
    TelegramNotificationTool,
    TelegramProviderResult,
)
from personal_assistant.application.ports.notifications import (
    NotificationMedia,
    NotificationRequest,
)
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.identity import Principal
from personal_assistant.domain.common.permissions import ApprovalGrant, PermissionTier


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.sent_audio: list[dict[str, object]] = []

    def send_message(self, *, chat_id: str, text: str) -> dict[str, int]:
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.sent)}

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, int]:
        self.sent_audio.append(
            {
                "chat_id": chat_id,
                "caption": caption,
                "filename": filename,
                "content_type": content_type,
                "data": data,
            }
        )
        return {"message_id": 100 + len(self.sent_audio)}


class OutcomeTelegramClient:
    def __init__(self, *outcomes: TelegramProviderResult) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def send_message(self, *, chat_id: str, text: str) -> TelegramProviderResult:
        self.calls += 1
        return self.outcomes.pop(0)

    def send_audio(
        self,
        *,
        chat_id: str,
        caption: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> TelegramProviderResult:
        self.calls += 1
        return self.outcomes.pop(0)


class FakeHttpResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers = Message()

    def __enter__(self) -> FakeHttpResponse:
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


class FailingReadBody(TrackingBody):
    def read(self, *args: object, **kwargs: object) -> bytes:
        raise OSError("private partial provider body")


class TelegramNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeTelegramClient()
        self.tool = TelegramNotificationTool(self.client)
        self.principal = Principal.for_test(
            principal_id="telegram-user-1",
            tenant_id="tenant-a",
            permission_tier=PermissionTier.P5,
        )
        self.request = NotificationRequest(
            channel="telegram",
            recipient="chat-1",
            body="Recordatorio",
            idempotency_key="msg-1",
        )

    def approval(self) -> ApprovalGrant:
        return ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=self.request.idempotency_key,
            tier=PermissionTier.P5,
        )

    def test_telegram_send_requires_p5_approval_before_dispatch_or_replay(self) -> None:
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
        self.assertEqual(sent.provider_message_id, 1)
        self.assertTrue(reused.reused)
        self.assertEqual(len(self.client.sent), 1)
        self.assertNotIn("recipient", sent.model_dump())

    def test_telegram_send_rejects_idempotency_conflict(self) -> None:
        self.tool.send(self.principal, self.request, approval=self.approval())
        conflict = self.request.model_copy(update={"body": "Otro texto"})

        with self.assertRaises(AssistantError) as ctx:
            self.tool.send(self.principal, conflict, approval=self.approval())

        self.assertEqual(ctx.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(len(self.client.sent), 1)

    def test_telegram_send_audio_uses_media_payload_and_idempotency(self) -> None:
        request = NotificationRequest(
            channel="telegram",
            recipient="chat-1",
            body="Listo, quedo agendado.",
            idempotency_key="msg-voice-1",
            media=NotificationMedia(
                filename="assistant-reply.mp3",
                content_type="audio/mpeg",
                data=b"mp3-bytes",
            ),
        )
        approval = ApprovalGrant.issue(
            principal=self.principal,
            action="notification.send",
            resource=request.idempotency_key,
            tier=PermissionTier.P5,
        )

        sent = self.tool.send(self.principal, request, approval=approval)
        reused = self.tool.send(self.principal, request, approval=approval)

        self.assertEqual(sent.notification_id, reused.notification_id)
        self.assertTrue(reused.reused)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(len(self.client.sent_audio), 1)
        self.assertEqual(self.client.sent_audio[0]["filename"], "assistant-reply.mp3")
        self.assertEqual(self.client.sent_audio[0]["data"], b"mp3-bytes")

    def test_unknown_outcome_is_not_implicitly_retried_and_conflicts_still_fail(
        self,
    ) -> None:
        client = OutcomeTelegramClient(
            TelegramProviderResult(outcome="unknown-outcome")
        )
        tool = TelegramNotificationTool(client)

        first = tool.send(self.principal, self.request, approval=self.approval())
        replay = tool.send(self.principal, self.request, approval=self.approval())

        self.assertEqual(first.outcome, "unknown-outcome")
        self.assertTrue(replay.reused)
        self.assertEqual(client.calls, 1)
        self.assertIsNone(first.notification_id)
        self.assertNotIn("recipient", first.model_dump())
        with self.assertRaises(AssistantError) as conflict:
            tool.send(
                self.principal,
                self.request.model_copy(update={"body": "payload distinto"}),
                approval=self.approval(),
            )
        self.assertEqual(conflict.exception.code, ErrorCode.CONFLICT)

    def test_known_transient_outcome_can_be_explicitly_attempted_again(self) -> None:
        client = OutcomeTelegramClient(
            TelegramProviderResult(
                outcome="known-transient", provider_code=429, retry_after=9
            ),
            TelegramProviderResult(outcome="success", provider_message_id=44),
        )
        tool = TelegramNotificationTool(client)

        first = tool.send(self.principal, self.request, approval=self.approval())
        with self.assertRaises(AssistantError) as conflict:
            tool.send(
                self.principal,
                self.request.model_copy(update={"body": "payload distinto"}),
                approval=self.approval(),
            )
        second = tool.send(self.principal, self.request, approval=self.approval())

        self.assertEqual(first.outcome, "known-transient")
        self.assertEqual(first.retry_after, 9)
        self.assertEqual(conflict.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(second.outcome, "success")
        self.assertEqual(client.calls, 2)


class TelegramBotApiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TelegramBotApiClient(token="secret-token")

    def send_message(self) -> TelegramProviderResult:
        return self.client.send_message(chat_id="private-chat", text="private text")

    def send_audio(self) -> TelegramProviderResult:
        return self.client.send_audio(
            chat_id="private-chat",
            caption="private caption",
            filename="private.mp3",
            content_type="audio/mpeg",
            data=b"private audio",
        )

    def test_send_message_and_audio_confirm_success_with_only_safe_metadata(
        self,
    ) -> None:
        response = FakeHttpResponse(b'{"ok":true,"result":{"message_id":123}}')
        with patch(
            "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
            return_value=response,
        ) as urlopen:
            for send in (self.send_message, self.send_audio):
                with self.subTest(send=send.__name__):
                    result = send()
                    self.assertEqual(result.outcome, "success")
                    self.assertEqual(result.provider_message_id, 123)
                    self.assertEqual(
                        set(result.model_dump()),
                        {
                            "outcome",
                            "provider_code",
                            "retry_after",
                            "provider_message_id",
                        },
                    )
        self.assertEqual(urlopen.call_count, 2)

    def test_http_429_uses_largest_valid_json_or_header_retry_after(self) -> None:
        headers = Message()
        headers["Retry-After"] = "12"
        body = TrackingBody(
            b'{"ok":false,"error_code":429,"description":"private",'
            b'"parameters":{"retry_after":7}}'
        )
        error = HTTPError(
            "https://redacted.invalid",
            429,
            "sensitive description",
            headers,
            body,
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.send_message()

        self.assertEqual(result.outcome, "known-transient")
        self.assertEqual(result.provider_code, 429)
        self.assertEqual(result.retry_after, 12)
        self.assertTrue(body.closed)
        self.assertEqual(body.close_calls, 1)
        serialized = repr(result.model_dump())
        self.assertNotIn("private", serialized)
        self.assertNotIn("sensitive", serialized)
        self.assertNotIn("redacted.invalid", serialized)

    def test_invalid_retry_after_values_are_discarded(self) -> None:
        headers = Message()
        headers["Retry-After"] = "0"
        error = HTTPError(
            "https://redacted.invalid",
            429,
            "ignored",
            headers,
            io.BytesIO(
                b'{"ok":false,"error_code":429,"parameters":{"retry_after":-4}}'
            ),
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.send_message()

        self.assertEqual(result.outcome, "known-transient")
        self.assertIsNone(result.retry_after)

    def test_large_positive_retry_after_is_preserved(self) -> None:
        large_retry = FakeHttpResponse(
            b'{"ok":false,"error_code":429,"parameters":{"retry_after":172800}}'
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
            return_value=large_retry,
        ):
            result = self.send_audio()

        self.assertEqual(result.outcome, "known-transient")
        self.assertEqual(result.retry_after, 172800)

    def test_http_error_body_is_closed_even_when_read_fails(self) -> None:
        body = FailingReadBody()
        error = HTTPError(
            "https://redacted.invalid",
            503,
            "ignored",
            Message(),
            body,
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.send_message()

        self.assertEqual(result.outcome, "known-transient")
        self.assertEqual(result.provider_code, 503)
        self.assertTrue(body.closed)
        self.assertEqual(body.close_calls, 1)

    def test_http_5xx_and_non_429_4xx_are_typed(self) -> None:
        cases = ((503, "known-transient"), (400, "permanent"))
        for status, expected in cases:
            with self.subTest(status=status):
                error = HTTPError(
                    "https://redacted.invalid",
                    status,
                    "ignored",
                    Message(),
                    io.BytesIO(b"not-json"),
                )
                with patch(
                    "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
                    side_effect=error,
                ):
                    result = self.send_audio()
                self.assertEqual(result.outcome, expected)
                self.assertEqual(result.provider_code, status)

    def test_explicit_http_status_wins_over_conflicting_body_code(self) -> None:
        error = HTTPError(
            "https://redacted.invalid",
            503,
            "ignored",
            Message(),
            io.BytesIO(b'{"ok":false,"error_code":400}'),
        )
        with patch(
            "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
            side_effect=error,
        ):
            result = self.send_message()

        self.assertEqual(result.outcome, "known-transient")
        self.assertEqual(result.provider_code, 503)

    def test_ok_false_is_classified_by_error_code_for_message_and_audio(self) -> None:
        cases = ((429, "known-transient"), (502, "known-transient"), (403, "permanent"))
        for send in (self.send_message, self.send_audio):
            for code, expected in cases:
                with self.subTest(send=send.__name__, code=code):
                    response = FakeHttpResponse(
                        (
                            '{"ok":false,"error_code":%d,"description":"private"}'
                            % code
                        ).encode()
                    )
                    with patch(
                        "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
                        return_value=response,
                    ):
                        result = send()
                    self.assertEqual(result.outcome, expected)
                    self.assertEqual(result.provider_code, code)

    def test_timeout_reset_and_url_error_are_unknown_outcomes(self) -> None:
        for send in (self.send_message, self.send_audio):
            for failure in (
                TimeoutError("private timeout"),
                ConnectionResetError("private reset"),
                IncompleteRead(b"private partial response"),
                URLError("private network detail"),
            ):
                with self.subTest(send=send.__name__, failure=type(failure).__name__):
                    with patch(
                        "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
                        side_effect=failure,
                    ):
                        result = send()
                    self.assertEqual(result.outcome, "unknown-outcome")
                    self.assertEqual(
                        result.model_dump(),
                        {
                            "outcome": "unknown-outcome",
                            "provider_code": None,
                            "retry_after": None,
                            "provider_message_id": None,
                        },
                    )

    def test_malformed_or_ambiguous_responses_are_unknown_outcomes(self) -> None:
        bodies = (
            b"not-json",
            b"[]",
            b"{}",
            b'{"ok":true}',
            b'{"ok":true,"result":{}}',
            b'{"ok":true,"result":{"message_id":"123"}}',
            b'{"ok":false}',
            b'{"ok":false,"error_code":"429"}',
        )
        for send in (self.send_message, self.send_audio):
            for body in bodies:
                with self.subTest(send=send.__name__, body=body):
                    with patch(
                        "personal_assistant.adapters.outbound.notifications.telegram.urllib_request.urlopen",
                        return_value=FakeHttpResponse(body),
                    ):
                        result = send()
                    self.assertEqual(result.outcome, "unknown-outcome")


if __name__ == "__main__":
    unittest.main()
