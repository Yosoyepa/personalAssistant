"""Strict schemas for versioned, executable evaluation cases."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
ExecutorSlug = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
]
CaseTier = Literal["golden", "failure-mode", "regression"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LegacySource(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> LegacySource:
        if len(self.ids) != len(set(self.ids)):
            raise ValueError("legacySource.ids must be unique")
        return self


class RetiredLegacyCase(StrictModel):
    id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    owner: str = Field(min_length=1)


class SuiteManifest(StrictModel):
    schemaVersion: Literal[1]
    suiteId: Slug
    caseFiles: list[str] = Field(min_length=1)
    legacySource: LegacySource | None = None
    retiredLegacyCases: list[RetiredLegacyCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_safe_files(self) -> SuiteManifest:
        if len(self.caseFiles) != len(set(self.caseFiles)):
            raise ValueError("caseFiles must be unique")
        for raw in self.caseFiles:
            path = Path(raw)
            if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
                raise ValueError(f"unsafe case file path: {raw}")
        retired_ids = [case.id for case in self.retiredLegacyCases]
        if len(retired_ids) != len(set(retired_ids)):
            raise ValueError("retiredLegacyCases ids must be unique")
        return self


class EvalCase(StrictModel):
    id: Slug
    category: Slug
    tier: CaseTier
    failureMode: Slug
    contractRefs: list[str] = Field(min_length=1)
    executor: ExecutorSlug
    input: dict[str, object]
    expected: dict[str, object]
    tags: list[Slug] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_tags(self) -> EvalCase:
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must be unique")
        if len(self.contractRefs) != len(set(self.contractRefs)):
            raise ValueError("contractRefs must be unique")
        if any(not reference.strip() for reference in self.contractRefs):
            raise ValueError("contractRefs entries must be non-empty")
        return self


class CaseFile(StrictModel):
    schemaVersion: Literal[1]
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> CaseFile:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique within a file")
        return self
