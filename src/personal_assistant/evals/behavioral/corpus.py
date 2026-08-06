"""Load and validate the human-labeled behavioral corpus.

Path handling mirrors `personal_assistant.evals.runner.load_suite`: label files
are named explicitly in a manifest, never globbed, and must resolve inside the
corpus root.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from personal_assistant.evals.behavioral.schema import (
    CorpusManifest,
    IntentLabel,
    LabelFile,
)


class CorpusValidationError(ValueError):
    """The corpus cannot be used because its declarative contract is invalid."""


@dataclass(frozen=True, slots=True)
class LabeledCorpus:
    corpus_id: str
    labels: tuple[IntentLabel, ...]

    def split(self, name: str) -> tuple[IntentLabel, ...]:
        return tuple(label for label in self.labels if label.split == name)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"cannot read valid JSON from {path}: {exc}") from exc


def load_corpus(corpus_dir: Path) -> LabeledCorpus:
    manifest_path = corpus_dir / "manifest.json"
    try:
        manifest = CorpusManifest.model_validate(_read_json(manifest_path))
    except ValidationError as exc:
        raise CorpusValidationError(
            f"invalid corpus manifest {manifest_path} ({exc.error_count()} schema errors)"
        ) from exc

    corpus_root = corpus_dir.resolve()
    labels: list[IntentLabel] = []
    for relative in manifest.labelFiles:
        label_path = (corpus_dir / relative).resolve()
        if corpus_root not in label_path.parents:
            raise CorpusValidationError(f"label file escapes corpus root: {relative}")
        try:
            label_file = LabelFile.model_validate(_read_json(label_path))
        except ValidationError as exc:
            raise CorpusValidationError(
                f"invalid label file {label_path} ({exc.error_count()} schema errors)"
            ) from exc
        labels.extend(label_file.labels)

    ids = [label.id for label in labels]
    duplicates = sorted({label_id for label_id in ids if ids.count(label_id) > 1})
    if duplicates:
        raise CorpusValidationError(
            f"duplicate label ids across corpus: {', '.join(duplicates)}"
        )
    if not any(label.split == "calibration" for label in labels):
        raise CorpusValidationError("corpus has no calibration split")
    if not any(label.split == "holdout" for label in labels):
        raise CorpusValidationError("corpus has no holdout split")
    return LabeledCorpus(corpus_id=manifest.corpusId, labels=tuple(labels))


def select(
    corpus: LabeledCorpus,
    *,
    splits: Iterable[str] = (),
    tags: Iterable[str] = (),
) -> tuple[IntentLabel, ...]:
    """Filter labels, refusing an empty selection the way the L1 runner does."""
    split_filter = set(splits)
    tag_filter = set(tags)
    selected = tuple(
        label
        for label in corpus.labels
        if (not split_filter or label.split in split_filter)
        and (not tag_filter or tag_filter.intersection(label.tags))
    )
    if not selected:
        raise CorpusValidationError("filters selected zero labels")
    return selected
