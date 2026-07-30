"""Structural and behavioral gate for the isolated security eval corpus."""

from __future__ import annotations

import json
from pathlib import Path

from personal_assistant.evals.executors.security_boundary_v1 import (
    ExpectedModel,
    InputModel,
    _response_safety,
    execute,
)
from personal_assistant.evals.schema import CaseFile


CORPUS = Path(__file__).parents[1] / "eval" / "cases" / "security-privacy.v1.json"
REQUIRED_CATEGORIES = {
    "authentication",
    "allowlist",
    "authorization",
    "tenant-isolation",
    "rejection-effects",
    "redaction",
}
REQUIRED_REDACTION_TAGS = {"message", "transcript", "token", "url", "audio", "error"}


def _cases() -> list[dict[str, object]]:
    decoded = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert decoded["schemaVersion"] == 1
    return decoded["cases"]


def test_security_privacy_corpus_has_unique_strict_cases_and_minimum_size() -> None:
    cases = _cases()
    CaseFile.model_validate_json(CORPUS.read_text(encoding="utf-8"))
    assert len(cases) >= 50
    ids = [str(case["id"]) for case in cases]
    assert len(ids) == len(set(ids))
    pairs = [
        json.dumps((case["input"], case["expected"]), sort_keys=True) for case in cases
    ]
    assert len(pairs) == len(set(pairs))
    assert "Ã" not in CORPUS.read_text(encoding="utf-8")
    for case in cases:
        assert set(case) <= {
            "id",
            "category",
            "tier",
            "failureMode",
            "contractRefs",
            "executor",
            "input",
            "expected",
            "tags",
        }
        assert all(
            case[name] for name in ("id", "category", "tier", "failureMode", "executor")
        )
        assert case["tier"] in {"golden", "failure-mode", "regression"}
        assert case["executor"] == "security.boundary.v1"
        assert isinstance(case["contractRefs"], list) and case["contractRefs"]
        InputModel.model_validate(case["input"])
        ExpectedModel.model_validate(case["expected"])


def test_security_privacy_corpus_covers_required_dimensions() -> None:
    cases = _cases()
    categories = {str(case["category"]) for case in cases}
    assert REQUIRED_CATEGORIES <= categories
    tags = {tag for case in cases for tag in case.get("tags", [])}
    assert {
        "loopback",
        "headers",
        "webhook",
        "p3",
        "p5",
        "default-deny",
        "tenant",
        "no-effects",
    } <= tags
    assert REQUIRED_REDACTION_TAGS <= tags
    failure_modes = {str(case["failureMode"]) for case in cases}
    assert len(failure_modes) == len(cases)


def test_security_privacy_cases_execute_real_boundaries() -> None:
    for case in _cases():
        input_model = InputModel.model_validate(case["input"])
        expected = ExpectedModel.model_validate(case["expected"]).model_dump(
            mode="json"
        )
        assert execute(input_model) == expected, case["id"]


def test_security_privacy_authority_and_preapproval_effect_invariants() -> None:
    by_input = {
        (case["input"]["scenario"], case["input"]["variant"]): case["expected"]
        for case in _cases()
    }
    reminder = by_input[("webhook-allow", "p3-reminder")]
    assert reminder["effects"] == {
        "approvals": 1,
        "calendar": 0,
        "scheduler": 0,
        "events": 0,
        "outbox": 0,
        "states": 1,
        # Four run events plus the needs-approval output guardrail scan
        # emitted by the phase-8 guardrail.checked wiring.
        "traces": 5,
    }
    assert reminder["authority"] == {
        "tenant": "fixture-tenant",
        "principal": "947362819",
        "tier": "P5",
        "provider": "telegram",
    }
    for variant in ("forged-headers", "forged-query"):
        observed = by_input[("local-authority", variant)]
        assert observed["authority"]["tenant"] == "fixture-tenant"
        assert observed["authority"]["principal"] == "fixture-user"
        assert observed["authority"]["tier"] == "P5"
    for case in _cases():
        if case["input"]["scenario"] == "trace-redaction":
            assert case["expected"]["authority"] is None


def test_numeric_actor_sentinel_does_not_match_trace_id_fragment() -> None:
    class Response:
        def __init__(self, text: str) -> None:
            self.text = text

    trace_fragment = _response_safety(Response('{"trace_id":"abc456def"}'), pii_sentinels=("456",))
    echoed_actor = _response_safety(Response('{"message":"actor 456 denied"}'), pii_sentinels=("456",))
    assert trace_fragment.pii_leaked is False
    assert echoed_actor.pii_leaked is True
