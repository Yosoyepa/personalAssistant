from __future__ import annotations

import unittest

from personal_assistant.evals.behavioral.metrics import (
    confusion_matrix,
    threshold_sweep,
    wilson_interval,
)


class WilsonIntervalTests(unittest.TestCase):
    def test_zero_successes_from_zero_total(self) -> None:
        interval = wilson_interval(0, 0)
        self.assertEqual(interval.low, 0.0)
        self.assertEqual(interval.high, 1.0)

    def test_all_successes(self) -> None:
        interval = wilson_interval(10, 10)
        self.assertGreater(interval.low, 0.6)
        # At p=1 the upper bound is analytically exactly 1; in floating point the
        # expression lands one ulp below, which `min(1.0, ...)` cannot lift back.
        # The reported value rounds to 1.0, so this is representation, not bias.
        self.assertAlmostEqual(interval.high, 1.0, places=12)
        self.assertLessEqual(interval.high, 1.0)

    def test_zero_successes_from_nonzero_total(self) -> None:
        interval = wilson_interval(0, 10)
        self.assertEqual(interval.low, 0.0)
        self.assertLess(interval.high, 0.4)

    def test_half_successes(self) -> None:
        interval = wilson_interval(50, 100)
        self.assertLess(interval.low, 0.5)
        self.assertGreater(interval.high, 0.5)

    def test_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            wilson_interval(-1, 10)

    def test_rejects_successes_exceeding_total(self) -> None:
        with self.assertRaises(ValueError):
            wilson_interval(11, 10)


class ConfusionMatrixTests(unittest.TestCase):
    def test_perfect_classifier(self) -> None:
        pairs = [(True, True), (True, True), (False, False), (False, False)]
        matrix = confusion_matrix(pairs)
        self.assertEqual(matrix.true_positives, 2)
        self.assertEqual(matrix.false_positives, 0)
        self.assertEqual(matrix.true_negatives, 2)
        self.assertEqual(matrix.false_negatives, 0)
        self.assertEqual(matrix.total, 4)
        self.assertEqual(matrix.true_positive_rate.value, 1.0)
        self.assertEqual(matrix.true_negative_rate.value, 1.0)

    def test_always_positive_classifier(self) -> None:
        pairs = [(True, True), (False, True), (True, True), (False, True)]
        matrix = confusion_matrix(pairs)
        self.assertEqual(matrix.true_positives, 2)
        self.assertEqual(matrix.false_positives, 2)
        self.assertEqual(matrix.true_negatives, 0)
        self.assertEqual(matrix.false_negatives, 0)
        self.assertEqual(matrix.true_positive_rate.value, 1.0)
        self.assertEqual(matrix.true_negative_rate.value, 0.0)

    def test_rate_with_no_condition_positives(self) -> None:
        # No "should accept" cases exist.
        pairs = [(False, False), (False, False), (False, True)]
        matrix = confusion_matrix(pairs)
        self.assertEqual(matrix.true_positive_rate.total, 0)
        self.assertIsNone(matrix.true_positive_rate.value)

    def test_serializes_to_dict(self) -> None:
        pairs = [(True, True), (False, False)]
        matrix = confusion_matrix(pairs)
        data = matrix.as_dict()
        self.assertIn("counts", data)
        self.assertIn("truePositiveRate", data)
        self.assertIsInstance(data["truePositiveRate"], dict)


class ThresholdSweepTests(unittest.TestCase):
    def test_sweeps_multiple_thresholds(self) -> None:
        scored = [(True, 0.9), (True, 0.6), (False, 0.3)]
        points = threshold_sweep(scored, [0.5, 0.7])
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].threshold, 0.5)
        self.assertEqual(points[1].threshold, 0.7)

    def test_threshold_order_does_not_matter(self) -> None:
        scored = [(True, 0.9), (True, 0.6)]
        points = threshold_sweep(scored, [0.9, 0.1, 0.5])
        thresholds = [p.threshold for p in points]
        self.assertEqual(thresholds, [0.1, 0.5, 0.9])

    def test_comparator_is_gte_to_match_runtime(self) -> None:
        scored = [(True, 0.65)]
        points = threshold_sweep(scored, [0.65])
        # The shipped threshold is 0.65, and `confidence >= 0.65` accepts it.
        self.assertEqual(points[0].matrix.true_positives, 1)

    def test_rejects_threshold_out_of_range(self) -> None:
        scored = [(True, 0.5)]
        with self.assertRaises(ValueError):
            threshold_sweep(scored, [1.5])

    def test_rejects_empty_thresholds(self) -> None:
        scored = [(True, 0.5)]
        with self.assertRaises(ValueError):
            threshold_sweep(scored, [])


if __name__ == "__main__":
    unittest.main()
