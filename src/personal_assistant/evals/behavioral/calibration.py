"""Pre-registered acceptance thresholds and the judge's authority decision.

These numbers were fixed in `docs/adr/ADR-006-behavioral-eval-tier-and-judge.md`
*before* any run produced a figure. That ordering is the whole point: a
threshold chosen after seeing the result is not a threshold, it is a
description. They live in code rather than only in the ADR so that promoting
the judge to a blocking gate requires editing a module with a test on it,
instead of re-reading a document and deciding it sounds fine.

`judge_authority` is deliberately hard to satisfy. It returns `blocking` only
when the evidence is a real recording, the holdout split is the one being
scored, both rates clear their pre-registered bar, and the sample actually
exercised both classes. Anything else is `advisory`, which is the state this
phase ships in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from personal_assistant.evals.behavioral.metrics import ConfusionMatrix

MIN_TRUE_POSITIVE_RATE = 0.90
MIN_TRUE_NEGATIVE_RATE = 0.90
"""Pre-registered in ADR-006 before the first run. Do not tune to a result."""

MIN_CLASS_SUPPORT = 10
"""Fewest labels of each class before a rate is allowed to decide anything.

A rate computed over zero negatives is not a low score, it is no score. The
phase-12 fixture run produced exactly that for the judge (TNR over n=0), and
without this floor a `None` would have to be interpreted somewhere downstream
-- most likely in the direction that flatters the judge.
"""

Authority = Literal["advisory", "blocking"]


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    """Why the judge does or does not get to fail a build."""

    authority: Authority
    reasons: tuple[str, ...]

    @property
    def is_blocking(self) -> bool:
        return self.authority == "blocking"

    def as_dict(self) -> dict[str, object]:
        return {"authority": self.authority, "reasons": list(self.reasons)}


def judge_authority(
    matrix: ConfusionMatrix | None,
    *,
    split: str,
    is_calibration_evidence: bool,
) -> AuthorityDecision:
    """Decide whether the judge may block, collecting every failed condition.

    Every reason is reported rather than short-circuiting on the first, because
    a reader deciding whether to promote the judge needs the full list of what
    is missing, not the first thing that happened to be checked.
    """
    reasons: list[str] = []

    if not is_calibration_evidence:
        reasons.append(
            "evidence is not a real recording (synthetic cassettes measure the "
            "harness, not a provider)"
        )
    if split != "holdout":
        reasons.append(f"scored on {split!r}, but only holdout may promote a judge")
    if matrix is None:
        reasons.append("no confusion matrix was produced")
        return AuthorityDecision(authority="advisory", reasons=tuple(reasons))

    tpr = matrix.true_positive_rate
    tnr = matrix.true_negative_rate
    if tpr.total < MIN_CLASS_SUPPORT:
        reasons.append(f"only {tpr.total} positive labels, need {MIN_CLASS_SUPPORT}")
    if tnr.total < MIN_CLASS_SUPPORT:
        reasons.append(f"only {tnr.total} negative labels, need {MIN_CLASS_SUPPORT}")
    if tpr.value is None or tpr.value < MIN_TRUE_POSITIVE_RATE:
        reasons.append(f"TPR {tpr.value} below pre-registered {MIN_TRUE_POSITIVE_RATE}")
    if tnr.value is None or tnr.value < MIN_TRUE_NEGATIVE_RATE:
        reasons.append(f"TNR {tnr.value} below pre-registered {MIN_TRUE_NEGATIVE_RATE}")

    authority: Authority = "advisory" if reasons else "blocking"
    return AuthorityDecision(authority=authority, reasons=tuple(reasons))
