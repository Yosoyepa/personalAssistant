"""Lock the pre-registered thresholds and the runtime constant they judge.

Two separate things are pinned here.

`LLM_INTENT_CONFIDENCE_THRESHOLD` is a *runtime* constant: changing it changes
what the product accepts from users. Phase 12 measures it and does not move it.
The test exists so that moving it later is a deliberate act with a failing test
attached, rather than a silent edit justified by a sweep someone ran once.

The ADR-006 acceptance bars are *pre-registered*: fixed before the first run so
they describe a standard rather than a result. A test that reads them from the
same module it checks would be circular, so the expected values are written out
as literals here -- that duplication is the point.
"""

from __future__ import annotations

import unittest

from personal_assistant.application.use_cases.commands import (
    LLM_INTENT_CONFIDENCE_THRESHOLD,
)
from personal_assistant.evals.behavioral.calibration import (
    MIN_CLASS_SUPPORT,
    MIN_TRUE_NEGATIVE_RATE,
    MIN_TRUE_POSITIVE_RATE,
    judge_authority,
)
from personal_assistant.evals.behavioral.metrics import (
    ConfusionMatrix,
    confusion_matrix,
)
from personal_assistant.evals.behavioral.runner import SWEEP_THRESHOLDS


def _matrix(*, tp: int, fn: int, tn: int, fp: int) -> ConfusionMatrix:
    pairs = (
        [(True, True)] * tp
        + [(True, False)] * fn
        + [(False, False)] * tn
        + [(False, True)] * fp
    )
    return confusion_matrix(pairs)


class RuntimeThresholdTests(unittest.TestCase):
    def test_runtime_threshold_is_still_the_measured_value(self) -> None:
        """Phase 12 reports on 0.65; it does not change runtime behavior."""
        self.assertEqual(LLM_INTENT_CONFIDENCE_THRESHOLD, 0.65)

    def test_sweep_contains_the_shipped_threshold(self) -> None:
        """Without this point the sweep cannot say what shipping costs."""
        self.assertIn(LLM_INTENT_CONFIDENCE_THRESHOLD, SWEEP_THRESHOLDS)

    def test_sweep_spans_the_usable_range_in_order(self) -> None:
        self.assertEqual(SWEEP_THRESHOLDS, sorted(SWEEP_THRESHOLDS))
        self.assertGreater(SWEEP_THRESHOLDS[0], 0.0)
        self.assertLess(SWEEP_THRESHOLDS[-1], 1.0)


class PreRegisteredBarTests(unittest.TestCase):
    def test_bars_match_adr_006_as_written(self) -> None:
        self.assertEqual(MIN_TRUE_POSITIVE_RATE, 0.90)
        self.assertEqual(MIN_TRUE_NEGATIVE_RATE, 0.90)
        self.assertEqual(MIN_CLASS_SUPPORT, 10)


class JudgeAuthorityTests(unittest.TestCase):
    """The judge may only block under conditions no current run satisfies."""

    def test_synthetic_evidence_can_never_block(self) -> None:
        decision = judge_authority(
            _matrix(tp=50, fn=0, tn=50, fp=0),
            split="holdout",
            is_calibration_evidence=False,
        )
        self.assertEqual(decision.authority, "advisory")
        self.assertTrue(any("not a real recording" in r for r in decision.reasons))

    def test_calibration_split_can_never_block(self) -> None:
        """A bar cleared on the split that tuned it is not a measurement."""
        decision = judge_authority(
            _matrix(tp=50, fn=0, tn=50, fp=0),
            split="calibration",
            is_calibration_evidence=True,
        )
        self.assertEqual(decision.authority, "advisory")
        self.assertTrue(any("only holdout" in r for r in decision.reasons))

    def test_absent_negative_class_can_never_block(self) -> None:
        """The exact hole in the phase-12 fixture run: TNR over n=0."""
        decision = judge_authority(
            _matrix(tp=50, fn=0, tn=0, fp=0),
            split="holdout",
            is_calibration_evidence=True,
        )
        self.assertEqual(decision.authority, "advisory")
        self.assertTrue(any("negative labels" in r for r in decision.reasons))

    def test_rate_below_the_bar_stays_advisory(self) -> None:
        decision = judge_authority(
            _matrix(tp=40, fn=10, tn=50, fp=0),
            split="holdout",
            is_calibration_evidence=True,
        )
        self.assertEqual(decision.authority, "advisory")
        self.assertTrue(any("TPR" in r for r in decision.reasons))

    def test_missing_matrix_stays_advisory(self) -> None:
        decision = judge_authority(None, split="holdout", is_calibration_evidence=True)
        self.assertEqual(decision.authority, "advisory")

    def test_every_failed_condition_is_reported(self) -> None:
        """A reader deciding to promote needs the whole list, not the first."""
        decision = judge_authority(
            _matrix(tp=2, fn=8, tn=0, fp=0),
            split="calibration",
            is_calibration_evidence=False,
        )
        self.assertGreaterEqual(len(decision.reasons), 4)

    def test_real_recording_clearing_both_bars_may_block(self) -> None:
        """The positive case, so the gate is not merely always-advisory."""
        decision = judge_authority(
            _matrix(tp=48, fn=2, tn=47, fp=3),
            split="holdout",
            is_calibration_evidence=True,
        )
        self.assertEqual(decision.authority, "blocking")
        self.assertEqual(decision.reasons, ())
        self.assertTrue(decision.is_blocking)


class ShippedRunAuthorityTests(unittest.TestCase):
    def test_the_committed_run_is_advisory_only(self) -> None:
        """End to end: today's evidence does not let the judge fail a build."""
        from pathlib import Path

        from personal_assistant.evals.behavioral.runner import run_corpus

        corpus = Path(__file__).resolve().parents[1] / "eval" / "behavioral"
        run = run_corpus(corpus, mode="replay")
        decision = run.judge_authority()
        self.assertEqual(decision.authority, "advisory")
        self.assertFalse(run.is_calibration_evidence)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
