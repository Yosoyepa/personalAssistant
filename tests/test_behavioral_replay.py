from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import LLMRequest, LLMResult
from personal_assistant.evals.behavioral.replay import (
    CassetteError,
    RecordingLLMProvider,
    ReplayLLMProvider,
    load_cassette,
    request_key,
    write_cassette,
)


class StubProvider:
    """Finite double standing in for a real provider during recording."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.calls: list[LLMRequest] = []
        self._data = data or {"kind": "reminder.create", "confidence": 0.9}

    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        self.calls.append(request)
        return LLMResult(
            provider="stub",
            model="stub-model-1",
            data=dict(self._data),
            input_tokens=11,
            output_tokens=7,
        )


def _request(prompt: str = "clasifica esto", **overrides: object) -> LLMRequest:
    payload: dict[str, object] = {
        "prompt": prompt,
        "schema_name": "conversation_intent",
        "max_tokens": 256,
        "temperature": 0.0,
    }
    payload.update(overrides)
    return LLMRequest(**payload)  # type: ignore[arg-type]  # reason: payload armado como dict genérico; el modelo valida en runtime


class RequestKeyTests(unittest.TestCase):
    def test_key_is_stable_across_equal_requests(self) -> None:
        self.assertEqual(request_key(_request()), request_key(_request()))

    def test_key_is_a_sha256_hex_digest(self) -> None:
        key = request_key(_request())
        self.assertEqual(len(key), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in key))

    def test_prompt_change_changes_the_key(self) -> None:
        self.assertNotEqual(request_key(_request("a")), request_key(_request("b")))

    def test_max_tokens_change_changes_the_key(self) -> None:
        self.assertNotEqual(
            request_key(_request()), request_key(_request(max_tokens=512))
        )

    def test_temperature_change_changes_the_key(self) -> None:
        self.assertNotEqual(
            request_key(_request()), request_key(_request(temperature=0.5))
        )

    def test_schema_name_change_changes_the_key(self) -> None:
        self.assertNotEqual(
            request_key(_request()),
            request_key(_request(schema_name="reminder_extraction")),
        )


class RecordingTests(unittest.TestCase):
    def test_records_and_passes_through_the_result(self) -> None:
        stub = StubProvider()
        recorder = RecordingLLMProvider(stub)
        result = recorder.complete(_request(), budget=TokenBudget(limit=1_000))
        self.assertEqual(result.model, "stub-model-1")
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(len(recorder.entries), 1)
        entry = recorder.entries[0]
        self.assertEqual(entry.provider, "stub")
        self.assertEqual(entry.inputTokens, 11)
        self.assertEqual(entry.outputTokens, 7)

    def test_repeated_identical_request_records_one_entry(self) -> None:
        recorder = RecordingLLMProvider(StubProvider())
        recorder.complete(_request(), budget=TokenBudget(limit=1_000))
        recorder.complete(_request(), budget=TokenBudget(limit=1_000))
        self.assertEqual(len(recorder.entries), 1)

    def test_refuses_to_build_an_empty_cassette(self) -> None:
        recorder = RecordingLLMProvider(StubProvider())
        with self.assertRaises(CassetteError):
            recorder.as_cassette(recorded_at="2026-08-06")


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def recorded_cassette_path(self) -> Path:
        recorder = RecordingLLMProvider(StubProvider())
        recorder.complete(_request(), budget=TokenBudget(limit=1_000))
        path = self.root / "cassette.json"
        write_cassette(path, recorder.as_cassette(recorded_at="2026-08-06"))
        return path

    def test_round_trips_a_recorded_response(self) -> None:
        path = self.recorded_cassette_path()
        provider = ReplayLLMProvider(load_cassette(path))
        result = provider.complete(_request(), budget=TokenBudget(limit=1_000))
        self.assertEqual(result.provider, "stub")
        self.assertEqual(result.model, "stub-model-1")
        self.assertEqual(result.data["kind"], "reminder.create")
        self.assertEqual(provider.served_keys, (request_key(_request()),))

    def test_missing_entry_raises_instead_of_skipping(self) -> None:
        path = self.recorded_cassette_path()
        provider = ReplayLLMProvider(load_cassette(path))
        with self.assertRaises(CassetteError) as caught:
            provider.complete(_request("un prompt no grabado"), budget=TokenBudget(limit=1_000))
        self.assertIn("re-record", str(caught.exception))

    def test_replay_is_byte_stable_across_writes(self) -> None:
        first = self.recorded_cassette_path()
        first_bytes = first.read_bytes()
        second = self.root / "again.json"
        recorder = RecordingLLMProvider(StubProvider())
        recorder.complete(_request(), budget=TokenBudget(limit=1_000))
        write_cassette(second, recorder.as_cassette(recorded_at="2026-08-06"))
        self.assertEqual(first_bytes, second.read_bytes())

    def test_cassette_is_written_with_lf_and_one_trailing_newline(self) -> None:
        raw = self.recorded_cassette_path().read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"}\n"))
        self.assertFalse(raw.endswith(b"\n\n"))

    def test_rejects_malformed_cassette_json(self) -> None:
        path = self.root / "broken.json"
        path.write_text("{not json", encoding="utf-8", newline="\n")
        with self.assertRaises(CassetteError):
            load_cassette(path)

    def test_rejects_cassette_with_unknown_field(self) -> None:
        path = self.recorded_cassette_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
        with self.assertRaises(CassetteError):
            load_cassette(path)

    def test_rejects_missing_cassette_file(self) -> None:
        with self.assertRaises(CassetteError):
            load_cassette(self.root / "absent.json")


if __name__ == "__main__":
    unittest.main()
