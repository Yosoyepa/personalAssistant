"""Confusion-matrix statistics for calibrating a binary classifier.

Pure functions only: no I/O, no provider access, no global state. Every rate is
returned with a Wilson score interval because the behavioral corpus is on the
order of a hundred labels, and at that size a bare point estimate hides an
interval wide enough to change the conclusion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Z_95 = 1.959963984540054
"""Two-sided normal quantile for a 95% interval."""


@dataclass(frozen=True, slots=True)
class Interval:
    low: float
    high: float

    def as_dict(self) -> dict[str, float]:
        return {"low": round(self.low, 4), "high": round(self.high, 4)}


@dataclass(frozen=True, slots=True)
class Rate:
    """A proportion, its denominator, and its uncertainty."""

    successes: int
    total: int
    interval: Interval

    @property
    def value(self) -> float | None:
        """``None`` when undefined, which is not the same as zero."""
        if self.total == 0:
            return None
        return self.successes / self.total

    def as_dict(self) -> dict[str, object]:
        return {
            "value": None if self.value is None else round(self.value, 4),
            "successes": self.successes,
            "total": self.total,
            "interval95": self.interval.as_dict(),
        }


def wilson_interval(successes: int, total: int, *, z: float = Z_95) -> Interval:
    """Return the Wilson score interval for ``successes`` out of ``total``.

    Wilson rather than the normal approximation: at n≈100 with a rate near 0.95
    the normal interval runs past 1.0, which is not a possible proportion.
    """
    if successes < 0 or total < 0:
        raise ValueError("counts must be non-negative")
    if successes > total:
        raise ValueError("successes cannot exceed total")
    if total == 0:
        return Interval(0.0, 1.0)
    proportion = successes / total
    denominator = 1.0 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
        / denominator
    )
    return Interval(max(0.0, center - margin), min(1.0, center + margin))


def _rate(successes: int, total: int) -> Rate:
    return Rate(successes, total, wilson_interval(successes, total))


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    """Counts of a binary decision against binary ground truth."""

    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int

    @property
    def total(self) -> int:
        return (
            self.true_positives
            + self.false_positives
            + self.true_negatives
            + self.false_negatives
        )

    @property
    def true_positive_rate(self) -> Rate:
        """Sensitivity: of the cases that should be accepted, how many were."""
        return _rate(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def true_negative_rate(self) -> Rate:
        """Specificity: of the cases that should be rejected, how many were."""
        return _rate(self.true_negatives, self.true_negatives + self.false_positives)

    @property
    def precision(self) -> Rate:
        return _rate(self.true_positives, self.true_positives + self.false_positives)

    @property
    def accuracy(self) -> Rate:
        return _rate(self.true_positives + self.true_negatives, self.total)

    def as_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "truePositives": self.true_positives,
                "falsePositives": self.false_positives,
                "trueNegatives": self.true_negatives,
                "falseNegatives": self.false_negatives,
                "total": self.total,
            },
            "truePositiveRate": self.true_positive_rate.as_dict(),
            "trueNegativeRate": self.true_negative_rate.as_dict(),
            "precision": self.precision.as_dict(),
            "accuracy": self.accuracy.as_dict(),
        }


def confusion_matrix(pairs: list[tuple[bool, bool]]) -> ConfusionMatrix:
    """Build a matrix from ``(expected, actual)`` decision pairs."""
    true_positives = sum(1 for expected, actual in pairs if expected and actual)
    false_positives = sum(1 for expected, actual in pairs if not expected and actual)
    true_negatives = sum(1 for expected, actual in pairs if not expected and not actual)
    false_negatives = sum(1 for expected, actual in pairs if expected and not actual)
    return ConfusionMatrix(
        true_positives=true_positives,
        false_positives=false_positives,
        true_negatives=true_negatives,
        false_negatives=false_negatives,
    )


@dataclass(frozen=True, slots=True)
class ThresholdPoint:
    threshold: float
    matrix: ConfusionMatrix

    def as_dict(self) -> dict[str, object]:
        return {"threshold": round(self.threshold, 4), **self.matrix.as_dict()}


def threshold_sweep(
    scored: list[tuple[bool, float]],
    thresholds: list[float],
) -> list[ThresholdPoint]:
    """Evaluate ``expected >= threshold`` decisions across candidate cutoffs.

    ``scored`` holds ``(expected, confidence)`` pairs. The comparison is ``>=``
    to match the runtime's own acceptance test, so a sweep point at the shipped
    threshold reproduces the shipped behavior exactly.
    """
    if not thresholds:
        raise ValueError("thresholds must not be empty")
    points: list[ThresholdPoint] = []
    for threshold in sorted(set(thresholds)):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold out of range: {threshold}")
        pairs = [(expected, score >= threshold) for expected, score in scored]
        points.append(ThresholdPoint(threshold, confusion_matrix(pairs)))
    return points
