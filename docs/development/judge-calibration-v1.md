# Behavioral Tier Calibration Report v1

**Date:** 2026-08-06
**Corpus:** `eval/behavioral` (`corpusId: behavioral`), 154 labels
**Mode:** `replay` against committed cassettes
**Cassette provenance:** `synthetic`
**Calibration evidence:** **no**
**Judge authority:** **advisory**

## Read this first

**Every rate in this document is circular and none of it describes a model.**

No live recording pass was possible. The environment has no provider configured
(`LLM_PROVIDER=disabled`, no API key, empty egress allowlist), so the committed
cassettes were produced by a fixture
(`scripts/build_synthetic_cassettes.py`) that answers each prompt from that
label's own `shouldAccept` field. A run over these cassettes reproduces the
labeler's intent, by construction. That is why the rates below are 1.0.

The tier is wired, verified, and reproducible. What it has not yet done is
measure anything. This document therefore publishes a **report format and a
harness verification**, not a calibration result.

`Cassette.provenance` is a required field with no default, and
`BehavioralRun.is_calibration_evidence` returns false for `synthetic`, so this
caveat is enforced in code rather than resting on this paragraph being read. See
ADR-006 §6.

## What was measured

| Surface | Runtime call site | Ground truth |
|---|---|---|
| `intent-classification` | `_infer_intent` (`use_cases/commands.py`), accepting on `confidence >= 0.65` | human `shouldAccept` |
| `reminder-extraction` | `_extract_with_llm` → `_reminder_extraction_from_llm` (`use_cases/reminders.py`) | human `shouldAccept` |
| `judge` | `judge_extraction` grading the extraction above | derived: did the runtime's accept/decline match the human's |

Label counts: 120 intent (70 calibration / 50 holdout), 34 extraction
(20 calibration / 14 holdout).

The judge's ground truth is **derived**, not directly labeled. The human said
whether the runtime *should* accept; agreement between that and what the runtime
actually did is whether the outcome was acceptable. This measures the judge on
the accept/decline decision only, never on field-level fidelity — the labels
predate the extractions, so no human ever graded a title or a timestamp.

## Rates (fixture, not evidence)

95% Wilson intervals. Reported per split; splits are never pooled, because a
threshold chosen on calibration and then scored on a pooled set would be
reporting partly on the data that chose it.

### intent-classification, at the shipped threshold 0.65

| Split | TPR | TNR |
|---|---|---|
| calibration | 1.000 [0.944, 1.000] (n=65) | 1.000 [0.566, 1.000] (n=5) |
| holdout | 1.000 [0.912, 1.000] (n=40) | 1.000 [0.723, 1.000] (n=10) |

Zero false positives and zero false negatives on both splits. Note how wide the
TNR interval is even at 1.0: with n=5, the lower bound is 0.566. At this sample
size a point estimate alone would be close to meaningless, which is why the
intervals are mandatory in this report rather than decorative.

### reminder-extraction

| Split | TPR | TNR |
|---|---|---|
| calibration | 1.000 [0.741, 1.000] (n=11) | 1.000 [0.701, 1.000] (n=9) |
| holdout | 1.000 [0.646, 1.000] (n=7) | 1.000 [0.646, 1.000] (n=7) |

### judge

| Split | TPR | TNR |
|---|---|---|
| calibration | 1.000 [0.839, 1.000] (n=20) | **undefined (n=0)** |
| holdout | 1.000 [0.785, 1.000] (n=14) | **undefined (n=0)** |

**The judge's negative path is completely untested.** Because the fixture always
makes the runtime agree with the label, `judge_expected` is never false, so
there are no negative cases at all. A TNR over n=0 is not a low score; it is no
score. Nothing in this run establishes that the judge can recognize an
unacceptable extraction — the failure mode that matters most for a judge.

`MIN_CLASS_SUPPORT = 10` exists so this hole cannot be waved through: a run with
no negatives can never promote the judge, regardless of how good its TPR looks.

### Secondary: intent kind agreement

0.400 [0.317, 0.489] (48/120). Also a fixture artifact — the synthetic provider
emits only `reminder.create` or `unsupported`, so any label expecting a third
kind disagrees automatically. It says nothing about the runtime.

## Threshold sweep for `LLM_INTENT_CONFIDENCE_THRESHOLD`

Swept 0.05–0.95 in 0.05 steps over all 120 intent labels. Abbreviated:

| Threshold | TPR | TNR | Accuracy | FP | FN |
|---|---|---|---|---|---|
| 0.05 – 0.30 | 1.000 | 0.000 | 0.875 | 15 | 0 |
| 0.35 – 0.90 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| **0.65 (shipped)** | **1.000** | **1.000** | **1.000** | **0** | **0** |
| 0.95 | 0.000 | 1.000 | 0.125 | 0 | 105 |

**This sweep is uninformative and must not be used to move the threshold.** The
fixture emits exactly two confidence values — 0.92 for accept and 0.30 for
decline — so the sweep is really finding the gap between those two constants.
Any threshold in (0.30, 0.92] scores identically. The apparent plateau is an
artifact of a two-point distribution, not evidence that the runtime is robust
across that range.

A real model produces a continuous spread of confidences, and the interesting
region is exactly where the fixture has no data. Per ADR-006 §7 and re-entry
trigger 4, `LLM_INTENT_CONFIDENCE_THRESHOLD` stays at **0.65**, unchanged.

## Pre-registered acceptance bars

Fixed in ADR-006 before the first run, encoded in
`evals/behavioral/calibration.py`, restated as literals in
`tests/test_intent_threshold_calibration.py`:

| Bar | Value |
|---|---|
| Minimum TPR | 0.90 |
| Minimum TNR | 0.90 |
| Minimum labels per class | 10 |
| Split that may promote | `holdout` only |
| Provenance that may promote | `recorded` only |

### Verdict: advisory

`judge_authority()` on this run returns `advisory` with:

- evidence is not a real recording (synthetic cassettes measure the harness, not
  a provider)
- only 0 negative labels, need 10
- TNR None below pre-registered 0.90

Note that the nominal rates (1.000 TPR) would clear the numeric bars. The
refusal comes from provenance and class support, which is the intended design:
**a flattering number cannot buy authority on its own.**

## What this phase does and does not establish

Established:

- The harness runs end to end against the runtime's own prompt renderers and
  parsers — not against a copy that could drift.
- Replay is deterministic: two `--json` runs are byte-identical (57,509 bytes).
- Replay needs no network: passes with an empty egress allowlist and
  `LLM_PROVIDER=disabled`.
- The L1 gate is uncontaminated: still 299/299.
- 154/154 labels complete with 0 harness errors.
- A missing cassette entry fails loudly (exit 1), never silently.
- Synthetic evidence cannot be promoted, enforced in code and tested.

Not established:

- **Any figure about any real model.** No live pass has run.
- **That the judge can reject a bad extraction.** TNR is undefined over n=0.
- **That the corpus reflects human judgement.** Every label carries
  `labeler: "assistant-draft"`. The labels were authored by the assistant, not
  reviewed by a maintainer. The audit row asks for calibration against *human*
  labels; that wording is not yet satisfied.
- **That 0.65 is a good threshold.** The sweep that would answer this needs a
  real confidence distribution.

## GAP #11 status

**Not closed.** The DoD asks for a ≥100-label set and a published TPR/TNR
report. The 154-label set exists and the report format is here, but its numbers
come from a fixture and its labels are assistant-drafted. Claiming the row on
this basis would be claiming a measurement that was never taken.

Two steps close it, in this order:

1. A maintainer reviews and re-labels the corpus, replacing `assistant-draft`.
2. A live recording pass produces `provenance: recorded` cassettes; this
   document is regenerated as v2 with real holdout rates and intervals, and
   `judge_authority()` is re-evaluated.

## Reproducing

```bash
uv run python -m personal_assistant.evals.behavioral \
  --corpus eval/behavioral --mode replay --json
```

Recording (requires a configured provider and an egress allowlist entry):

```bash
uv run python -m personal_assistant.evals.behavioral \
  --corpus eval/behavioral --mode record
```

A `record` run writes `provenance: recorded` by default. The synthetic fixtures
are only produced by `scripts/build_synthetic_cassettes.py`, which passes
`provenance="synthetic"` explicitly — marking a cassette synthetic is always a
deliberate act by a caller that knows its provider is a fixture.

