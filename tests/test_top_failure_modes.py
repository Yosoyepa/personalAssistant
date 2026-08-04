"""Registry lock for the canonical top failure modes (audit GAP #10)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from personal_assistant.evals.runner import load_suite, run_suite

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUITE = REPOSITORY_ROOT / "eval" / "cases"

CANONICAL_BY_FILE = {
    "atomicity-recovery-postgres.v1.json": "reminder-atomicity-violation",
    "delivery-concurrency-postgres.v1.json": "delivery-concurrency-violation",
    "idempotency.v2.json": "idempotency-replay-duplicate",
    "temporal.v1.json": "temporal-misinterpretation",
    "security-privacy.v1.json": "security-boundary-breach",
}
ALL_UNIQUE_DETAIL_FILES = {
    "security-privacy.v1.json",
    "delivery-concurrency-postgres.v1.json",
}


def _cases_by_file() -> dict[str, list]:
    _, cases = load_suite(SUITE)
    return {
        name: [case for case in cases if case.failureMode == canonical]
        for name, canonical in CANONICAL_BY_FILE.items()
    }


def test_five_canonical_slugs_are_exactly_the_registry() -> None:
    by_file = _cases_by_file()
    canonical_slugs = set(CANONICAL_BY_FILE.values())

    assert set(by_file) == set(CANONICAL_BY_FILE)
    assert len(canonical_slugs) == 5
    for name, cases in by_file.items():
        assert cases, f"{name} produced no canonical cases"
        assert {case.failureMode for case in cases} == {CANONICAL_BY_FILE[name]}


def test_each_canonical_slug_has_at_least_fifty_cases() -> None:
    _, cases = load_suite(SUITE)
    counts = Counter(case.failureMode for case in cases)

    for canonical in CANONICAL_BY_FILE.values():
        assert counts[canonical] >= 50, canonical


def test_canonical_cases_keep_non_empty_failure_mode_detail() -> None:
    for name, cases in _cases_by_file().items():
        assert all(case.failureModeDetail for case in cases), name


def test_detail_uniqueness_is_preserved_where_fine_slugs_were_unique() -> None:
    by_file = _cases_by_file()
    for name in ALL_UNIQUE_DETAIL_FILES:
        details = [case.failureModeDetail for case in by_file[name]]
        assert len(details) == len(set(details)), name


def test_top_mode_filter_selects_the_temporal_family() -> None:
    run = run_suite(SUITE, failure_modes=["temporal-misinterpretation"])

    assert run.selected == 60
    assert run.failed == 0


def test_detail_filter_selects_the_dst_gap_cases() -> None:
    run = run_suite(SUITE, failure_mode_details=["dst-gap"])

    assert run.selected == 2
    assert run.failed == 0
