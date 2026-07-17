from __future__ import annotations

import json
import unittest

from personal_assistant.adapters.observability.local import TraceRecorder
from personal_assistant.application.dto.tracing import TraceEvent, TraceEventType
from personal_assistant.domain.common.exceptions import AssistantError, ErrorCode
from personal_assistant.domain.common.privacy import REDACTED, REDACTED_URL


TEXT_FIXTURE = "temporary fixture utterance"
TRANSCRIPT_FIXTURE = "fixture transcription content"
CREDENTIAL_FIXTURE = "test_placeholder_credential"
QUERY_FIXTURE = "test_placeholder_query"
URL_FIXTURE = (
    "https://fixture-user:test-placeholder@example.invalid/audio"
    f"?access_token={QUERY_FIXTURE}#fragment"
)
AUDIO_FIXTURE = b"test-only-audio-bytes"


class TracePrivacyTests(unittest.TestCase):
    def test_trace_serialization_is_recursive_allowlisted_and_idempotent(self) -> None:
        trace = TraceEvent(
            trace_id="trace-fixture",
            run_id=URL_FIXTURE,
            agent_id="personal_assistant",
            event_type=TraceEventType.agent_failed,
            tenant_id="tenant-fixture",
            input_summary={
                "TeXt": TEXT_FIXTURE,
                "MeTaDaTa": {
                    "items": [
                        {
                            "Authorization": f"Bearer {CREDENTIAL_FIXTURE}",
                            "request_URL": URL_FIXTURE,
                            "audio": AUDIO_FIXTURE,
                            "opaque": AUDIO_FIXTURE,
                            "unknown_future_field": TEXT_FIXTURE,
                        }
                    ]
                },
            },
            output_summary={"TrAnScRiPt": TRANSCRIPT_FIXTURE},
            error={
                "TyPe": "FixtureError",
                "MeSsAgE": f"failed token={CREDENTIAL_FIXTURE}",
            },
        )

        first = trace.model_dump(mode="json")
        second = trace.for_persistence().model_dump(mode="json")
        serialized = json.dumps(second, sort_keys=True)

        self.assertEqual(first, second)
        for unsafe in (
            TEXT_FIXTURE,
            TRANSCRIPT_FIXTURE,
            CREDENTIAL_FIXTURE,
            QUERY_FIXTURE,
            "fixture-user",
            "test-placeholder",
        ):
            self.assertNotIn(unsafe, serialized)
        self.assertTrue(first["run_id"].startswith("sha256:"))
        self.assertEqual(first["input_summary"]["TeXt"], REDACTED)
        self.assertEqual(first["output_summary"]["TrAnScRiPt"], REDACTED)
        self.assertEqual(first["error"]["MeSsAgE"], REDACTED)
        nested = first["input_summary"]["MeTaDaTa"]["items"][0]
        self.assertEqual(nested["Authorization"], REDACTED)
        self.assertEqual(nested["request_URL"], REDACTED_URL)
        self.assertEqual(nested["audio"]["kind"], "binary")
        self.assertEqual(nested["audio"]["size_bytes"], len(AUDIO_FIXTURE))
        self.assertEqual(len(nested["audio"]["sha256"]), 64)
        self.assertNotIn("opaque", nested)
        self.assertNotIn("unknown_future_field", nested)

    def test_in_memory_recorder_enforces_boundary_and_returns_safe_copies(self) -> None:
        recorder = TraceRecorder()
        trace = TraceEvent(
            trace_id="trace-memory",
            run_id="run-memory",
            agent_id="personal_assistant",
            event_type=TraceEventType.tool_called,
            tenant_id="tenant-fixture",
        )
        trace.input_summary["message"] = TEXT_FIXTURE
        trace.output_summary["audio"] = AUDIO_FIXTURE
        self.assertNotIn(TEXT_FIXTURE, f"{trace!s} {trace!r}")

        recorder.write(trace)
        trace.input_summary["message"] = TRANSCRIPT_FIXTURE

        [stored] = recorder.list_for_tenant("tenant-fixture")
        stored_payload = stored.model_dump_json()
        self.assertNotIn(TEXT_FIXTURE, stored_payload)
        self.assertNotIn(TRANSCRIPT_FIXTURE, stored_payload)
        self.assertNotIn(AUDIO_FIXTURE.decode(), stored_payload)
        self.assertEqual(stored.input_summary["message"], REDACTED)
        self.assertEqual(
            stored.output_summary["audio"]["size_bytes"], len(AUDIO_FIXTURE)
        )

        stored.error["ClientSecret"] = CREDENTIAL_FIXTURE
        [stored_again] = recorder.list_for_tenant("tenant-fixture")
        self.assertNotIn("ClientSecret", stored_again.error)
        self.assertNotIn(CREDENTIAL_FIXTURE, stored_again.model_dump_json())

    def test_structured_errors_redact_serialization_and_exception_text(self) -> None:
        error = AssistantError(
            ErrorCode.INTERNAL_ERROR,
            (
                f"transcript: {TRANSCRIPT_FIXTURE}; "
                f"token={CREDENTIAL_FIXTURE} at {URL_FIXTURE}"
            ),
            tenant_id="tenant-fixture",
            context={
                "idempotency_key": "fixture-idempotency",
                "errors": [
                    {
                        "input": TEXT_FIXTURE,
                        "loc": ["body", "message"],
                        "type": "string_type",
                    }
                ],
                "MeTaDaTa": {
                    "items": [
                        {
                            "ApiKey": CREDENTIAL_FIXTURE,
                            "endpoint_url": URL_FIXTURE,
                            "unknown_future_field": TEXT_FIXTURE,
                        }
                    ]
                },
            },
        )
        error.response.error.context["ClientSecret"] = CREDENTIAL_FIXTURE

        serialized = json.dumps(error.model_dump(), sort_keys=True)
        rendered_exception = (
            f"{error!s} {error!r} {error.response!s} {error.response.error!r}"
        )

        for unsafe in (
            CREDENTIAL_FIXTURE,
            QUERY_FIXTURE,
            "fixture-user",
            "test-placeholder",
            TEXT_FIXTURE,
            TRANSCRIPT_FIXTURE,
        ):
            self.assertNotIn(unsafe, serialized)
            self.assertNotIn(unsafe, rendered_exception)
        self.assertIn(REDACTED, serialized)
        self.assertIn(REDACTED_URL, serialized)
        self.assertEqual(
            error.response.error.context["idempotency_key"], "fixture-idempotency"
        )
        self.assertEqual(error.response.error.context["errors"][0]["input"], REDACTED)

        tampered_response = error.response.model_copy(
            update={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR,
                    "message": f"message={TEXT_FIXTURE}",
                    "context": {"Transcript": TRANSCRIPT_FIXTURE},
                }
            }
        )
        tampered_serialized = tampered_response.model_dump_json()
        self.assertNotIn(TEXT_FIXTURE, tampered_serialized)
        self.assertNotIn(TRANSCRIPT_FIXTURE, tampered_serialized)
        self.assertNotIn(TEXT_FIXTURE, repr(tampered_response))


if __name__ == "__main__":
    unittest.main()
