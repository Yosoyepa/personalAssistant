"""Release-gate contracts for the versioned deterministic eval runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from personal_assistant.evals.__main__ import main
from personal_assistant.evals.executors import legacy_pytest_v1
from personal_assistant.evals.runner import SuiteValidationError, load_suite, run_suite


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUITE = REPOSITORY_ROOT / "eval" / "cases"


def temporal_case(*, case_id: str = "runner-example", expected_hour: int = 17) -> dict:
    return {
        "id": case_id,
        "category": "temporal",
        "tier": "golden",
        "failureMode": "absolute-wall-clock",
        "contractRefs": ["AC-05"],
        "executor": "reminder.extract.v1",
        "input": {
            "text": "recuérdame mañana a las 17",
            "now": "2026-06-20T12:00:00Z",
            "timezone": "UTC",
        },
        "expected": {
            "status": "parsed",
            "reason": None,
            "timezone": "UTC",
            "startsAt": f"2026-06-21T{expected_hour:02d}:00:00Z",
            "notifyAt": None,
        },
        "tags": ["runner"],
    }


def write_suite(tmp_path: Path, cases_by_file: list[list[dict]]) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(parents=True)
    files = []
    for index, cases in enumerate(cases_by_file):
        name = f"cases-{index}.json"
        files.append(name)
        (suite / name).write_text(
            json.dumps({"schemaVersion": 1, "cases": cases}), encoding="utf-8"
        )
    (suite / "suite.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "suiteId": "runner-tests",
                "caseFiles": files,
            }
        ),
        encoding="utf-8",
    )
    return suite


def test_repository_suite_migrates_legacy_and_executes_sixty_temporal_cases() -> None:
    manifest, cases = load_suite(SUITE)
    ids = {case.id for case in cases}
    assert manifest.legacySource is not None
    legacy_ids = set(manifest.legacySource.ids)
    active_legacy = [case for case in cases if case.id in legacy_ids]
    retired_ids = {case.id for case in manifest.retiredLegacyCases}

    assert sum(case.executor == "reminder.extract.v1" for case in cases) == 60
    assert len(active_legacy) == 26
    assert all("migrated" in case.tags for case in active_legacy)
    assert "golden-contract-a2a-serializable" in ids
    assert retired_ids == {"regression-PROD-0001-placeholder"}
    assert "regression-PROD-0001-placeholder" not in ids


def test_legacy_cases_keep_semantic_filter_buckets_and_protocol_probes() -> None:
    _, cases = load_suite(SUITE)
    legacy = [case for case in cases if "migrated" in case.tags]
    protocol = {case.id: case for case in legacy if case.category == "protocol-policy"}
    pytest_nodes = {
        str(case.input["testNode"])
        for case in legacy
        if case.executor == "legacy.pytest.v1"
    }

    assert len({case.category for case in legacy}) >= 10
    assert len({case.failureMode for case in legacy}) == len(legacy)
    assert pytest_nodes == legacy_pytest_v1.MIGRATED_TEST_NODES
    assert protocol["failure-mcp-tools-forbidden-in-mvp"].input == {
        "toolName": "mcp.search"
    }
    assert protocol["failure-a2a-tools-forbidden-in-mvp"].input == {
        "toolName": "a2a.delegate"
    }
    assert run_suite(SUITE, failure_modes=["mcp-tool-invocation"]).passed == 1
    assert run_suite(SUITE, failure_modes=["a2a-tool-invocation"]).passed == 1


def test_legacy_executor_rejects_syntactically_valid_unallowlisted_node() -> None:
    with pytest.raises(ValidationError, match="immutable legacy migration"):
        legacy_pytest_v1.InputModel.model_validate_json(
            json.dumps(
                {
                    "testNode": (
                        "tests/test_contracts.py::ContractTests::"
                        "test_not_part_of_the_migration"
                    )
                }
            )
        )


def test_legacy_executor_scrubs_inherited_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("API_TOKEN", "must-not-leak")
    monkeypatch.setenv("SERVICE_SECRET", "must-not-leak")
    monkeypatch.setenv("PRIVATE_KEY", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("TEST_POSTGRES_DSN", "postgresql://secret")
    monkeypatch.setattr(legacy_pytest_v1.subprocess, "run", fake_run)
    value = legacy_pytest_v1.InputModel(
        testNode=next(iter(sorted(legacy_pytest_v1.MIGRATED_TEST_NODES)))
    )

    assert legacy_pytest_v1.execute(value) == {"passed": True}
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert set(environment).issubset(
        {
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUTF8",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
        }
    )
    assert not any(
        name.endswith(("_TOKEN", "_SECRET", "_KEY"))
        or name in {"DATABASE_URL", "TEST_POSTGRES_DSN"}
        for name in environment
    )
    assert captured["timeout"] == 60
    assert captured["stdout"] is legacy_pytest_v1.subprocess.DEVNULL
    assert captured["stderr"] is legacy_pytest_v1.subprocess.DEVNULL


def test_temporal_corpus_is_utf8_without_mojibake() -> None:
    content = (SUITE / "temporal.v1.json").read_text(encoding="utf-8")

    assert "recuérdame mañana" in content
    assert "Ã" not in content
    assert "Â" not in content


def test_filters_select_category_tier_and_failure_mode() -> None:
    category = run_suite(SUITE, categories=["temporal"])
    tier = run_suite(SUITE, categories=["temporal"], tiers=["golden"])
    failure_mode = run_suite(SUITE, failure_modes=["dst-gap"])

    assert category.selected == 61
    assert tier.selected == 33
    assert failure_mode.selected == 2
    assert failure_mode.failed == 0


def test_zero_case_filter_is_not_a_vacuous_pass() -> None:
    with pytest.raises(SuiteValidationError, match="zero cases"):
        run_suite(SUITE, categories=["does-not-exist"])


def test_wrong_expected_value_is_binary_failure_without_value_leak(tmp_path: Path) -> None:
    suite = write_suite(tmp_path, [[temporal_case(expected_hour=18)]])

    result = run_suite(suite)

    assert result.failed == 1
    assert result.results[0].errors == ("output mismatch",)
    assert "2026" not in repr(result.results[0].errors)


def test_json_cli_is_machine_only_and_returns_nonzero_on_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = write_suite(tmp_path, [[temporal_case(expected_hour=18)]])

    exit_code = main(["--suite", str(suite), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert captured.err == ""
    assert payload["summary"] == {"selected": 1, "passed": 0, "failed": 1}


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        json.dumps({"schemaVersion": 1, "cases": [{**temporal_case(), "extra": 1}]}),
        json.dumps({"schemaVersion": 99, "cases": [temporal_case()]}),
    ],
)
def test_corrupt_case_files_are_rejected(tmp_path: Path, payload: str) -> None:
    suite = write_suite(tmp_path, [[temporal_case()]])
    (suite / "cases-0.json").write_text(payload, encoding="utf-8")

    with pytest.raises(SuiteValidationError, match="case file|valid JSON"):
        load_suite(suite)


@pytest.mark.parametrize("case_files", [["../outside.json"], ["C:/outside.json"], ["x.json", "x.json"]])
def test_manifest_rejects_unsafe_or_duplicate_case_files(
    tmp_path: Path, case_files: list[str]
) -> None:
    suite = write_suite(tmp_path, [[temporal_case()]])
    (suite / "suite.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "suiteId": "runner-tests", "caseFiles": case_files}
        ),
        encoding="utf-8",
    )

    with pytest.raises(SuiteValidationError, match="manifest"):
        load_suite(suite)


def test_resolved_case_file_escape_is_rejected(tmp_path: Path) -> None:
    suite = write_suite(tmp_path, [[temporal_case()]])
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schemaVersion": 1, "cases": [temporal_case()]}))
    link = suite / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this Windows host")
    (suite / "suite.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "suiteId": "runner-tests", "caseFiles": ["linked.json"]}
        )
    )

    with pytest.raises(SuiteValidationError, match="escapes suite root"):
        load_suite(suite)


def test_unknown_executor_and_invalid_executor_contract_are_rejected(
    tmp_path: Path,
) -> None:
    unknown = temporal_case()
    unknown["category"] = "security"
    unknown["executor"] = "unknown.executor.v1"
    with pytest.raises(SuiteValidationError, match="cannot load executor"):
        run_suite(
            write_suite(tmp_path / "unknown", [[unknown]]),
            categories=["temporal"],
        )

    invalid = temporal_case()
    del invalid["input"]["now"]
    with pytest.raises(SuiteValidationError, match="violates executor"):
        run_suite(write_suite(tmp_path / "invalid", [[invalid]]))


def test_duplicate_ids_across_files_are_rejected(tmp_path: Path) -> None:
    duplicate = temporal_case(case_id="duplicate-global")
    suite = write_suite(tmp_path, [[duplicate], [duplicate]])

    with pytest.raises(SuiteValidationError, match="duplicate case ids across suite"):
        load_suite(suite)


def test_legacy_hash_or_inventory_corruption_is_rejected(tmp_path: Path) -> None:
    suite = write_suite(tmp_path, [[temporal_case()]])
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps([{"id": "legacy-one"}]), encoding="utf-8")
    digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
    manifest = json.loads((suite / "suite.json").read_text())
    manifest["legacySource"] = {
        "path": "../legacy.json",
        "sha256": digest,
        "ids": ["different-id"],
    }
    (suite / "suite.json").write_text(json.dumps(manifest))

    with pytest.raises(SuiteValidationError, match="migration inventory"):
        load_suite(suite)


def test_missing_active_legacy_case_cannot_silently_pass(tmp_path: Path) -> None:
    suite = write_suite(tmp_path, [[temporal_case(case_id="legacy-one")]])
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps([{"id": "legacy-one"}, {"id": "legacy-two"}]),
        encoding="utf-8",
    )
    manifest = json.loads((suite / "suite.json").read_text())
    manifest["legacySource"] = {
        "path": "../legacy.json",
        "sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
        "ids": ["legacy-one", "legacy-two"],
    }
    (suite / "suite.json").write_text(json.dumps(manifest))

    with pytest.raises(SuiteValidationError, match="exactly active or retired"):
        load_suite(suite)
