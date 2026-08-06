"""Strict schemas for the human-labeled behavioral corpus and its cassettes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from personal_assistant.evals.schema import Slug, StrictModel

Split = Literal["calibration", "holdout"]
"""Calibration labels may inform thresholds; holdout labels only measure them."""

Surface = Literal["intent-classification", "reminder-extraction"]
"""The two real LLM call sites in the runtime."""


class IntentLabel(StrictModel):
    """A human judgement about what the runtime should do with one message.

    ``expectedKind`` is the command the message should resolve to and
    ``shouldAccept`` is whether the runtime ought to act on that classification
    at all. They are independent: a message can be a recognizable reminder that
    is still too vague to act on, which is exactly the region the runtime's
    confidence threshold is supposed to separate.
    """

    id: Slug
    split: Split
    text: str = Field(min_length=1, max_length=500)
    expectedKind: str = Field(min_length=1)
    shouldAccept: bool
    labeler: str = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=500)
    tags: list[Slug] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_tags(self) -> IntentLabel:
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must be unique")
        return self


class LabelFile(StrictModel):
    schemaVersion: Literal[1]
    surface: Surface
    labels: list[IntentLabel] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids_and_texts(self) -> LabelFile:
        ids = [label.id for label in self.labels]
        if len(ids) != len(set(ids)):
            raise ValueError("label ids must be unique within a file")
        texts = [label.text.strip().casefold() for label in self.labels]
        if len(texts) != len(set(texts)):
            raise ValueError("label texts must be unique within a file")
        return self


class CorpusManifest(StrictModel):
    schemaVersion: Literal[1]
    corpusId: Slug
    labelFiles: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_safe_files(self) -> CorpusManifest:
        if len(self.labelFiles) != len(set(self.labelFiles)):
            raise ValueError("labelFiles must be unique")
        for raw in self.labelFiles:
            parts = raw.split("/")
            if raw.startswith("/") or ".." in parts or not raw.endswith(".json"):
                raise ValueError(f"unsafe label file path: {raw}")
        return self


class CassetteEntry(StrictModel):
    """One recorded provider response, keyed by the request that produced it."""

    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    schemaName: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    data: dict[str, object]
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)


class Cassette(StrictModel):
    schemaVersion: Literal[1]
    recordedAt: str = Field(min_length=1)
    entries: list[CassetteEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_keys(self) -> Cassette:
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("cassette keys must be unique")
        return self
