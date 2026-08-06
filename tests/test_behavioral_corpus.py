from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from personal_assistant.evals.behavioral.corpus import (
    CorpusValidationError,
    load_corpus,
    select,
)


def _label(
    label_id: str,
    *,
    split: str = "calibration",
    text: str | None = None,
    kind: str = "reminder.create",
    accept: bool = True,
    tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": label_id,
        "split": split,
        "text": text or f"texto de prueba {label_id}",
        "expectedKind": kind,
        "shouldAccept": accept,
        "labeler": "maintainer",
        "rationale": "caso sintetico de prueba",
        "tags": tags or [],
    }


class CorpusLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, manifest: dict[str, object], files: dict[str, object]) -> Path:
        (self.root / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8", newline="\n"
        )
        for name, payload in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
        return self.root

    def valid_corpus(self) -> Path:
        return self.write(
            {"schemaVersion": 1, "corpusId": "behavioral", "labelFiles": ["intent.v1.json"]},
            {
                "intent.v1.json": {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [
                        _label("a", split="calibration", tags=["relative-time"]),
                        _label("b", split="holdout", accept=False, kind="unsupported"),
                    ],
                }
            },
        )

    def test_loads_labels_from_manifest(self) -> None:
        corpus = load_corpus(self.valid_corpus())
        self.assertEqual(corpus.corpus_id, "behavioral")
        self.assertEqual(len(corpus.labels), 2)
        self.assertEqual(len(corpus.split("calibration")), 1)
        self.assertEqual(len(corpus.split("holdout")), 1)

    def test_rejects_missing_manifest(self) -> None:
        with self.assertRaises(CorpusValidationError):
            load_corpus(self.root)

    def test_rejects_label_file_escaping_root(self) -> None:
        root = self.write(
            {
                "schemaVersion": 1,
                "corpusId": "behavioral",
                "labelFiles": ["nested/../intent.v1.json"],
            },
            {},
        )
        with self.assertRaises(CorpusValidationError):
            load_corpus(root)

    def test_rejects_absolute_label_file(self) -> None:
        root = self.write(
            {"schemaVersion": 1, "corpusId": "behavioral", "labelFiles": ["/etc/passwd.json"]},
            {},
        )
        with self.assertRaises(CorpusValidationError):
            load_corpus(root)

    def test_rejects_duplicate_ids_across_files(self) -> None:
        root = self.write(
            {
                "schemaVersion": 1,
                "corpusId": "behavioral",
                "labelFiles": ["one.json", "two.json"],
            },
            {
                "one.json": {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [_label("dup", split="calibration")],
                },
                "two.json": {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [_label("dup", split="holdout", text="otro texto")],
                },
            },
        )
        with self.assertRaises(CorpusValidationError):
            load_corpus(root)

    def test_rejects_duplicate_text_differing_only_by_case_and_padding(self) -> None:
        root = self.write(
            {"schemaVersion": 1, "corpusId": "behavioral", "labelFiles": ["intent.v1.json"]},
            {
                "intent.v1.json": {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [
                        _label("a", text="Recuerdame algo"),
                        _label("b", split="holdout", text="  RECUERDAME ALGO  "),
                    ],
                }
            },
        )
        with self.assertRaises(CorpusValidationError):
            load_corpus(root)

    def test_rejects_corpus_without_holdout(self) -> None:
        root = self.write(
            {"schemaVersion": 1, "corpusId": "behavioral", "labelFiles": ["intent.v1.json"]},
            {
                "intent.v1.json": {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [_label("a"), _label("b", text="otro")],
                }
            },
        )
        with self.assertRaises(CorpusValidationError):
            load_corpus(root)

    def test_rejects_unknown_field(self) -> None:
        root = self.write(
            {"schemaVersion": 1, "corpusId": "behavioral", "labelFiles": ["intent.v1.json"]},
            {
                "intent.v1.json": {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [{**_label("a"), "unexpected": True}],
                }
            },
        )
        with self.assertRaises(CorpusValidationError):
            load_corpus(root)


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "corpusId": "behavioral",
                    "labelFiles": ["intent.v1.json"],
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        (root / "intent.v1.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "surface": "intent-classification",
                    "labels": [
                        _label("a", split="calibration", tags=["relative-time"]),
                        _label("b", split="holdout", tags=["dst"]),
                    ],
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.corpus = load_corpus(root)

    def test_filters_by_split(self) -> None:
        selected = select(self.corpus, splits=["holdout"])
        self.assertEqual([label.id for label in selected], ["b"])

    def test_filters_by_tag(self) -> None:
        selected = select(self.corpus, tags=["relative-time"])
        self.assertEqual([label.id for label in selected], ["a"])

    def test_empty_selection_is_an_error_not_an_empty_pass(self) -> None:
        with self.assertRaises(CorpusValidationError):
            select(self.corpus, splits=["calibration"], tags=["dst"])


if __name__ == "__main__":
    unittest.main()
