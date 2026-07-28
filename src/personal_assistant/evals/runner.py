"""Load, validate, filter, and execute deterministic eval cases."""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ValidationError
from personal_assistant.evals.schema import CaseFile, EvalCase, SuiteManifest


class SuiteValidationError(ValueError):
    """The suite cannot run because its declarative contract is invalid."""


@dataclass(frozen=True, slots=True)
class CaseResult:
    id: str
    passed: bool
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"id": self.id, "passed": self.passed, "errors": list(self.errors)}


@dataclass(frozen=True, slots=True)
class EvalRun:
    suite_id: str
    selected: int
    passed: int
    failed: int
    results: tuple[CaseResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "suiteId": self.suite_id,
            "summary": {
                "selected": self.selected,
                "passed": self.passed,
                "failed": self.failed,
            },
            "results": [result.as_dict() for result in self.results],
        }


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteValidationError(f"cannot read valid JSON from {path}: {exc}") from exc


def _canonical_text_digest(content: bytes) -> str:
    """Hash text with repository-canonical LF line endings on every platform."""
    canonical = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def _validate_legacy(manifest: SuiteManifest, suite_dir: Path) -> None:
    legacy = manifest.legacySource
    if legacy is None:
        return
    source = (suite_dir / legacy.path).resolve()
    if suite_dir.parent.resolve() not in source.parents:
        raise SuiteValidationError("legacySource.path escapes the suite directory")
    try:
        content = source.read_bytes()
        payload = json.loads(content)
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteValidationError(f"invalid legacy source {source}: {exc}") from exc
    digest = _canonical_text_digest(content)
    if digest != legacy.sha256:
        raise SuiteValidationError(
            f"legacy source hash mismatch: expected {legacy.sha256}, got {digest}"
        )
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise SuiteValidationError("legacy source must be a JSON array of objects")
    ids = [item.get("id") for item in payload]
    if ids != legacy.ids:
        raise SuiteValidationError("legacy source IDs differ from the migration inventory")


def load_suite(suite_dir: Path) -> tuple[SuiteManifest, tuple[EvalCase, ...]]:
    manifest_path = suite_dir / "suite.json"
    try:
        manifest = SuiteManifest.model_validate(_read_json(manifest_path))
    except ValidationError as exc:
        raise SuiteValidationError(
            f"invalid suite manifest {manifest_path} ({exc.error_count()} schema errors)"
        ) from exc

    _validate_legacy(manifest, suite_dir)
    cases: list[EvalCase] = []
    suite_root = suite_dir.resolve()
    for relative in manifest.caseFiles:
        case_path = (suite_dir / relative).resolve()
        if suite_root not in case_path.parents:
            raise SuiteValidationError(f"case file escapes suite root: {relative}")
        try:
            case_file = CaseFile.model_validate(_read_json(case_path))
        except ValidationError as exc:
            raise SuiteValidationError(
                f"invalid case file {case_path} ({exc.error_count()} schema errors)"
            ) from exc
        cases.extend(case_file.cases)
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise SuiteValidationError(f"duplicate case ids across suite: {', '.join(duplicates)}")
    retired_ids = {case.id for case in manifest.retiredLegacyCases}
    if retired_ids.intersection(ids):
        raise SuiteValidationError("retired legacy cases cannot also be active")
    if retired_ids and (
        manifest.legacySource is None
        or not retired_ids.issubset(manifest.legacySource.ids)
    ):
        raise SuiteValidationError("retired legacy cases must exist in legacySource.ids")
    if manifest.legacySource is not None:
        legacy_ids = set(manifest.legacySource.ids)
        accounted_legacy_ids = legacy_ids.intersection(ids) | retired_ids
        if accounted_legacy_ids != legacy_ids:
            raise SuiteValidationError(
                "every legacy source ID must be exactly active or retired"
            )
    return manifest, tuple(cases)


def _executor_module(case: EvalCase):
    module_leaf = case.executor.replace(".", "_")
    module_name = f"personal_assistant.evals.executors.{module_leaf}"
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise SuiteValidationError(f"cannot load executor {case.executor!r}") from exc
    for attribute in ("InputModel", "ExpectedModel", "execute"):
        if not hasattr(module, attribute):
            raise SuiteValidationError(
                f"executor {case.executor!r} does not export {attribute}"
            )
    if not issubclass(module.InputModel, BaseModel) or not issubclass(
        module.ExpectedModel, BaseModel
    ):
        raise SuiteValidationError(
            f"executor {case.executor!r} schemas must be Pydantic models"
        )
    for schema in (module.InputModel, module.ExpectedModel):
        if schema.model_config.get("extra") != "forbid" or not schema.model_config.get(
            "strict"
        ):
            raise SuiteValidationError(
                f"executor {case.executor!r} schemas must be strict and forbid extras"
            )
    if not callable(module.execute):
        raise SuiteValidationError(f"executor {case.executor!r} execute is not callable")
    return module


def _validated_case(case: EvalCase):
    module = _executor_module(case)
    try:
        executor_input = module.InputModel.model_validate_json(json.dumps(case.input))
        expected = module.ExpectedModel.model_validate_json(json.dumps(case.expected))
    except ValidationError as exc:
        raise SuiteValidationError(
            f"case {case.id!r} violates executor {case.executor!r} "
            f"({exc.error_count()} schema errors)"
        ) from exc
    return module, executor_input, expected


def _run_case(case: EvalCase) -> CaseResult:
    module, executor_input, expected_model = _validated_case(case)
    try:
        actual = module.execute(executor_input)
    except Exception as exc:  # the gate reports a sanitized case failure
        return CaseResult(
            id=case.id,
            passed=False,
            errors=(f"executor raised {type(exc).__name__}",),
        )
    expected = expected_model.model_dump(mode="json")
    if not isinstance(actual, dict):
        return CaseResult(case.id, False, ("executor returned invalid output",))
    try:
        actual_json = json.dumps(actual, ensure_ascii=False)
        canonical_actual = module.ExpectedModel.model_validate_json(
            actual_json
        ).model_dump(mode="json")
    except (TypeError, ValueError, ValidationError):
        return CaseResult(case.id, False, ("executor returned invalid output",))
    errors = () if canonical_actual == expected else ("output mismatch",)
    return CaseResult(id=case.id, passed=not errors, errors=errors)


def _matches(value: str, filters: set[str]) -> bool:
    return not filters or value in filters


def run_suite(
    suite_dir: Path,
    *,
    categories: Iterable[str] = (),
    tiers: Iterable[str] = (),
    failure_modes: Iterable[str] = (),
) -> EvalRun:
    manifest, cases = load_suite(suite_dir)
    for case in cases:
        _validated_case(case)
    category_filter = set(categories)
    tier_filter = set(tiers)
    failure_mode_filter = set(failure_modes)
    selected = tuple(
        case
        for case in cases
        if _matches(case.category, category_filter)
        and _matches(case.tier, tier_filter)
        and _matches(case.failureMode, failure_mode_filter)
    )
    if not selected:
        raise SuiteValidationError("filters selected zero cases")
    results = tuple(_run_case(case) for case in selected)
    passed = sum(result.passed for result in results)
    return EvalRun(
        suite_id=manifest.suiteId,
        selected=len(results),
        passed=passed,
        failed=len(results) - passed,
        results=results,
    )
