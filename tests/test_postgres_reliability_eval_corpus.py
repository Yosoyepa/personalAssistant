"""Inventory and blocking execution gates for the PostgreSQL reliability corpus."""

from __future__ import annotations

import json
from pathlib import Path

from personal_assistant.evals.executors import reminder_atomicity_postgres_v1
from personal_assistant.evals.runner import load_suite, run_suite

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "eval" / "cases"
ATOMIC_FILE = "atomicity-recovery-postgres.v1.json"
DELIVERY_FILE = "delivery-concurrency-postgres.v1.json"
RELIABILITY_CATEGORIES = {"atomicity-recovery", "delivery-concurrency"}


def _semantic_signature(case: object) -> str:
    """Ignore labels and random-data seeds; retain every executed dimension."""

    executor = case.executor
    inputs = dict(case.input)
    inputs.pop("variant", None)
    if executor == "reminder.atomicity.postgres.v1":
        inputs.setdefault("recoveryProcess", "same-process")
    if executor == "outbox.delivery.postgres.v1":
        inputs.setdefault("providerCodeMode", "present")
        inputs.setdefault("tenantProbe", "transition")
    return json.dumps(
        {
            "executor": executor,
            "input": inputs,
            "expected": case.expected,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def test_reliability_files_are_explicit_and_have_required_distribution() -> None:
    manifest, cases = load_suite(SUITE)
    assert manifest.caseFiles[-2:] == [ATOMIC_FILE, DELIVERY_FILE]
    reliability = [case for case in cases if case.category in RELIABILITY_CATEGORIES]
    atomic = [case for case in reliability if case.category == "atomicity-recovery"]
    delivery = [case for case in reliability if case.category == "delivery-concurrency"]

    assert len(atomic) == 50
    assert len(delivery) == 50
    assert {case.executor for case in atomic} == {
        "reminder.atomicity.postgres.v1"
    }
    assert {case.executor for case in delivery} == {
        "outbox.delivery.postgres.v1"
    }
    assert all(case.executor != "legacy.pytest.v1" for case in reliability)
    assert all("PG16-REAL" in case.contractRefs for case in reliability)
    assert all("passed" not in case.expected for case in reliability)


def test_reliability_cases_have_no_cosmetic_semantic_duplicates() -> None:
    _, cases = load_suite(SUITE)
    reliability = [case for case in cases if case.category in RELIABILITY_CATEGORIES]
    signatures = [_semantic_signature(case) for case in reliability]
    assert len(signatures) == len(set(signatures)) == 100


def test_atomicity_executor_rejects_unexpected_faults_and_keeps_utf8_source() -> None:
    source = Path(reminder_atomicity_postgres_v1.__file__).read_text(encoding="utf-8")
    assert "recuérdame clase mañana a las 17" in source
    assert "Ã" not in source
    assert "except Exception:" not in source
    assert source.count("if not _is_injected_database_fault(error):") == 2
    assert not reminder_atomicity_postgres_v1._is_injected_database_fault(
        RuntimeError("eval injected write fault")
    )


def test_missing_dsn_is_a_sanitized_blocking_failure(monkeypatch: object) -> None:
    monkeypatch.delenv("TEST_POSTGRES_DSN", raising=False)  # type: ignore[attr-defined]  # reason: el módulo no importa pytest; delenv existe en la fixture MonkeyPatch
    result = run_suite(SUITE, categories=["atomicity-recovery"])

    assert result.selected == 50
    assert result.passed == 0
    assert result.failed == 50
    assert {
        error
        for case_result in result.results
        for error in case_result.errors
    } == {"executor raised MissingTestPostgresDsnError"}


def test_complete_postgres_reliability_corpus_passes() -> None:
    result = run_suite(
        SUITE,
        categories=["atomicity-recovery", "delivery-concurrency"],
    )

    assert result.selected == 100
    assert result.passed == 100
    assert result.failed == 0
