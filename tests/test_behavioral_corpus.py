from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from personal_assistant.application.use_cases.commands import (
    _allowed_free_text_intents,
)
from personal_assistant.domain.reminders.parser import (
    REMINDER_TRIGGERS,
    _fold_text,
)
from personal_assistant.evals.behavioral.corpus import (
    CorpusValidationError,
    load_corpus,
    select,
)
from personal_assistant.evals.behavioral.schema import IntentLabel

SHIPPED_CORPUS = Path(__file__).resolve().parents[1] / "eval" / "behavioral"


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


class ShippedCorpusTests(unittest.TestCase):
    """Guard the claims `docs/development/judge-labeling-rubric.md` makes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus(SHIPPED_CORPUS)

    def intent_labels(self) -> tuple[IntentLabel, ...]:
        return tuple(label for label in self.corpus.labels if label.id.startswith("ic-"))

    def extraction_labels(self) -> tuple[IntentLabel, ...]:
        return tuple(label for label in self.corpus.labels if label.id.startswith("re-"))

    def test_meets_the_scorecard_minimum_of_one_hundred_labels(self) -> None:
        # Row 11 of the readiness scorecard asks for >=100 human labels. The
        # intent surface alone has to clear it, so the number does not depend
        # on counting two surfaces together.
        self.assertGreaterEqual(len(self.intent_labels()), 100)

    def test_intent_kinds_stay_inside_the_prompt_allowlist(self) -> None:
        allowed = {kind.value for kind in _allowed_free_text_intents()}
        for label in self.intent_labels():
            with self.subTest(label=label.id):
                self.assertIn(label.expectedKind, allowed)

    def test_extraction_labels_reach_the_llm_path(self) -> None:
        # `_extract_with_llm` only runs when the deterministic parser returns
        # `not_a_reminder`, which happens only when the folded text contains no
        # trigger. A label with a trigger would measure a path the runtime never
        # takes for it.
        for label in self.extraction_labels():
            folded = _fold_text(label.text)
            hits = [trigger for trigger in REMINDER_TRIGGERS if trigger in folded]
            with self.subTest(label=label.id):
                self.assertEqual(hits, [])

    def test_both_splits_carry_both_classes(self) -> None:
        # Without both classes in holdout, one of TPR or TNR is undefined there
        # and the published calibration would be half a report.
        for split in ("calibration", "holdout"):
            decisions = {label.shouldAccept for label in self.corpus.split(split)}
            with self.subTest(split=split):
                self.assertEqual(decisions, {True, False})

    def test_ambiguous_labels_are_never_marked_acceptable(self) -> None:
        # Rubric section 4, rule 5: no referent in a single-shot system means
        # the runtime must not act. Flipping one of these would inflate TPR.
        ambiguous = [
            label for label in self.corpus.labels if "ambiguous" in label.tags
        ]
        self.assertGreater(len(ambiguous), 0)
        for label in ambiguous:
            with self.subTest(label=label.id):
                self.assertFalse(label.shouldAccept)

    def test_every_label_is_traceable_to_a_labeler_and_a_reason(self) -> None:
        for label in self.corpus.labels:
            with self.subTest(label=label.id):
                self.assertTrue(label.labeler.strip())
                self.assertTrue(label.rationale.strip())


if __name__ == "__main__":
    unittest.main()
