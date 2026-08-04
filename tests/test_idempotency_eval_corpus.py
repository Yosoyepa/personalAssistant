"""Integrity checks for the declarative v2 idempotency corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_assistant.evals.executors.reminder_idempotency_v2 import (
    ExpectedModel,
    InputModel,
    execute,
)

CORPUS_PATH = (
    Path(__file__).resolve().parents[1] / "eval" / "cases" / "idempotency.v2.json"
)


def _cases() -> list[dict[str, object]]:
    document = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == 1
    assert set(document) == {"schemaVersion", "cases"}
    return document["cases"]


def test_idempotency_corpus_has_unique_ids_and_required_case_metadata() -> None:
    cases = _cases()

    raw_corpus = CORPUS_PATH.read_text(encoding="utf-8")
    assert "Ã" not in raw_corpus
    assert "Ì" not in raw_corpus
    assert "�" not in raw_corpus

    assert len(cases) >= 50
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["executor"] == "reminder.idempotency.v2"
        assert case["tier"] in {"golden", "failure-mode", "regression"}
        assert case["failureMode"]
        assert case["contractRefs"]
        InputModel.model_validate(case["input"])
        ExpectedModel.model_validate(case["expected"])


def test_idempotency_corpus_has_no_duplicate_input_and_expected_pairs() -> None:
    pairs = [
        json.dumps(
            {"input": case["input"], "expected": case["expected"]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for case in _cases()
    ]

    assert len(pairs) == len(set(pairs))


def test_idempotency_corpus_covers_all_v2_dimensions_and_transports() -> None:
    cases = _cases()
    categories = {case["category"] for case in cases}

    assert {
        "replay",
        "tenant",
        "channel",
        "actor",
        "conversation",
        "event",
        "payload-fingerprint",
        "adversarial-collision",
        "deterministic-effect-ids",
        "http-conflict",
        "telegram-conflict",
    } <= categories
    transports = {case["input"]["transport"] for case in cases}
    assert transports == {"store", "http", "telegram"}
    assert all(
        case["expected"]["transportStatuses"] == []
        for case in cases
        if case["input"]["transport"] == "store"
    )
    assert all(
        len(case["expected"]["transportStatuses"]) == 2
        for case in cases
        if case["input"]["transport"] != "store"
    )
    assert any(case["expected"]["transportStatuses"] == [202, 409] for case in cases)
    assert any(case["expected"]["transportStatuses"] == [200, 200] for case in cases)
    tenant_cases = [case for case in cases if case["category"] == "tenant"]
    assert tenant_cases
    assert all(case["expected"]["crossTenantVisibleRows"] == 0 for case in tenant_cases)
    assert all(case["expected"]["firstTenantWorkflowRows"] == 1 for case in tenant_cases)
    assert all(case["expected"]["secondTenantWorkflowRows"] == 1 for case in tenant_cases)


def test_cases_execute_against_production_idempotency_contracts() -> None:
    for case in _cases():
        actual = execute(InputModel.model_validate(case["input"]))
        expected = ExpectedModel.model_validate(case["expected"]).model_dump(mode="json")
        assert actual == expected, case["id"]


def test_transport_input_shapes_reject_invalid_boundary_identities() -> None:
    first = _cases()[-1]["input"]["first"]
    malformed_telegram = {
        "transport": "telegram",
        "first": first,
        "second": {**first, "sourceEventId": "provider-update"},
    }
    malformed_http = {
        "transport": "http",
        "first": {**first, "principalId": "user-1"},
        "second": {**first, "principalId": "user-2"},
    }

    with pytest.raises(ValidationError):
        InputModel.model_validate(malformed_telegram)
    with pytest.raises(ValidationError):
        InputModel.model_validate(malformed_http)
