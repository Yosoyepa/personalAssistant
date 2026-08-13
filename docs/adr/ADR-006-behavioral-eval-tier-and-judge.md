# ADR-006: Behavioral Eval Tier and the Authority of an LLM Judge

## Status

Accepted

Delivered in phase 12
(`docs/development/hardening/phase-12-behavioral-evals-judge.md`).

This ADR addresses GAP #11 of the production-readiness audit
(`docs/development/production-readiness-v0.2.0-alpha.1.md`). It does **not**
declare that row closed: the DoD asks for a calibration report over human
labels, and this phase ships the harness, the corpus, and the report format
while two conditions remain unmet. Both are stated in Consequences.

## Date

2026-08-06

## Context

The Level-1 release gate is 299 deterministic cases and **not one of them
exercises an LLM**. The 60 `temporal-misinterpretation` cases run
`extract_reminder()`, the rule-based parser
(`evals/executors/reminder_extract_v1.py`). `eval/README.md` forbids LLM judges
in L1 outright.

The runtime has exactly two LLM call sites, and both were unmeasured:

| Surface | Call site | Why it was unmeasured |
|---|---|---|
| `reminder-extraction` | `use_cases/reminders.py` (`_extract_with_llm`) | Fires *only* when the deterministic parser returns `UnsupportedReminder(not_a_reminder)`. The LLM therefore handles exactly the input the L1 corpus does not cover. |
| `intent-classification` | `use_cases/commands.py` (`_infer_intent`) | Accepts on `confidence >= LLM_INTENT_CONFIDENCE_THRESHOLD`, where that constant is `0.65` — chosen by hand and never checked against data. |

So the project had a well-tested deterministic shell around an unmeasured LLM
core, and a decision threshold with no evidence behind it.

## Decision

### 1. A separate tier, not an extension of L1

The behavioral tier is a distinct package (`evals/behavioral/`) and a distinct
corpus (`eval/behavioral/`). L1 stays deterministic and LLM-free.

`evals/runner.py` decides with exact equality (`canonical_actual == expected`).
That is the right rule for a release gate and the wrong rule for graded human
judgement. Rather than loosen it, the tiers stay apart. **The L1 prohibition on
LLM judges remains in force** and the L1 gate must keep reporting 299/299 — a
change in that number is the signal that the tiers have contaminated each other.

### 2. Record/replay at the `LLMProvider` seam

`LLMProvider` is a single-method Protocol and `LLMResult` is a closed,
serializable model, so a cassette entry is a dumped `LLMResult` keyed by
sha256 of `(schema_name, prompt, temperature, max_tokens)`.

CI runs `--mode replay` only, needs no network, and blocks on **replay
determinism and harness contract**. A missing cassette entry in replay mode is a
hard sanitized failure — never a skip, never an empty pass. This is the same
policy `eval/README.md` applies to an absent `TEST_POSTGRES_DSN`, for the same
reason: a gate that quietly degrades to zero assertions reports success for work
it did not do.

### 3. The runner calls the runtime's own prompts and parsers

The runner imports `_render_intent_prompt`,
`_render_reminder_extraction_prompt` and `_reminder_extraction_from_llm` from
the application layer. These private imports are deliberate couplings. An eval
that renders its own copy of a prompt keeps passing while the shipped prompt
drifts away from it, which is precisely the regression this tier exists to
catch.

`CORPUS_NOW` is pinned (2026-03-02 14:00Z). Every prompt embeds the current
instant and the cassette key hashes the prompt, so a moving clock would
invalidate every recorded response on every run.

### 4. Disagreement is a measurement, not a failure

Exit 1 covers harness failures only: a cassette that cannot answer, a payload
the runtime's own parser rejects, a judge that broke. A run in which the model
disagrees with every human label exits 0.

Wiring disagreement to the release gate would make CI depend on a provider
matching one person's judgement. A judge that broke is likewise scored as an
error rather than a FAIL, because scoring it as a genuine rejection would
flatter the true-negative rate.

### 5. Pre-registered bars, enforced in code

Fixed **before** the first run, and therefore a standard rather than a
description of a result:

| Bar | Value |
|---|---|
| Minimum true positive rate | 0.90 |
| Minimum true negative rate | 0.90 |
| Minimum labels per class | 10 |
| Split that may promote | `holdout` only |
| Provenance that may promote | `recorded` only |

These live in `evals/behavioral/calibration.py` with
`tests/test_intent_threshold_calibration.py` restating them as literals. The
duplication is intentional: a test that read the bars from the module it checks
would assert nothing. `judge_authority()` returns `advisory` unless **every**
condition holds, and reports all failed conditions rather than the first, so a
reader deciding whether to promote sees the whole list.

### 6. Provenance is required and has no default

`Cassette.provenance` is `Literal["recorded", "synthetic"]`, required, with no
default value. A cassette that forgets to say what it is would otherwise inherit
the flattering answer. `BehavioralRun.is_calibration_evidence` is true only for
`recorded`, and the CLI prints a warning above the rates when it is false.

### 7. The runtime threshold is measured, not moved

Phase 12 reports a sweep across 0.05–0.95 including 0.65. It does **not** change
`LLM_INTENT_CONFIDENCE_THRESHOLD`. Moving it changes what the product accepts
from users, so it requires its own change with its own rollback plan — not a
side effect of an eval phase.

## Consequences

### The judge is advisory-only, and the code says so

The shipped run returns `authority: "advisory"` with these reasons:

- evidence is not a real recording
- only 0 negative labels, need 10
- TNR None below pre-registered 0.90

### Two conditions block the GAP #11 claim

1. **No live recording pass happened.** This environment has no provider
   configured: `LLM_PROVIDER=disabled`, no API key, and an empty egress
   allowlist. The committed cassettes are stamped `synthetic` and were produced
   by a fixture that answers from each label's own `shouldAccept`. Every rate
   they yield is therefore circular by construction, which is why they are all
   1.0. **No TPR/TNR figure about any real model may be published from them.**
2. **The labels are assistant-drafted.** Every label carries
   `labeler: "assistant-draft"`. The audit row says "calibrated to *human*
   labels". A maintainer review pass is required before that wording is
   satisfied.

### The judge's negative path is untested

Because the fixture always makes the runtime agree with the label,
`judge_expected` is never false and judge TNR is undefined over n=0. A real
recording pass is the only thing that fills this in.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Put the judge in the L1 gate | Breaks exact-equality determinism and the `eval/README.md` prohibition; makes the release gate depend on a provider. |
| Let a missing cassette skip the label | A gate that degrades to zero assertions reports success for work it did not do. |
| Default `provenance` to `recorded` | An unlabeled cassette would inherit the flattering answer. |
| Move `LLM_INTENT_CONFIDENCE_THRESHOLD` now, from the sweep | The sweep ran against synthetic cassettes. Changing runtime acceptance on fixture evidence is worse than leaving a hand-picked constant in place and saying so. |
| Record with the Claude Code session credentials present in the environment | They are the operator's tooling credentials, not project configuration, and `agentrouter.org` is not in the project's egress allowlist. Using them would spend someone else's quota and violate ADR-004. |
| Publish the 1.0 rates as the calibration result | They measure the fixture author. This is the specific dishonesty provenance exists to prevent. |

## Re-entry triggers

Reopen this ADR when any of these occurs:

1. **A live recording pass is performed.** Re-record cassettes with
   `provenance: recorded`, recompute holdout rates with Wilson intervals, and
   re-evaluate `judge_authority()` — it may legitimately return `blocking`.
2. **A maintainer reviews the corpus labels**, replacing `assistant-draft` with
   a human labeler and making the "human labels" DoD wording accurate.
3. **The judge is proposed as a blocking gate.** Requires a recorded holdout run
   clearing both bars with ≥10 labels per class, and a decision on what a judge
   outage does to the build.
4. **`LLM_INTENT_CONFIDENCE_THRESHOLD` is proposed for change.** Requires a
   recorded sweep, not a synthetic one, plus a rollback plan.
5. **A prompt under `prompts/` changes.** The cassette key hashes the rendered
   prompt, so every cassette misses and must be re-recorded. The replay failure
   is the intended alarm.
6. **`CORPUS_NOW` changes**, for the same reason.
7. **Multi-turn conversation history is introduced.** This tier assumes
   single-shot prompts; that change reopens ADR-005 first.

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| L1 gate uncontaminated | `python -m personal_assistant.evals --suite eval/cases` reports 299/299 |
| Replay is deterministic | two `--json` runs are byte-identical |
| Replay needs no network | passes with an empty egress allowlist and `LLM_PROVIDER=disabled` |
| Corpus is ≥100 labels | 154 selected, 154 completed, 0 errored |
| Missing cassette entry fails loudly | `tests/test_behavioral_cli.py` asserts exit 1 with a `CassetteError` |
| Synthetic cassettes cannot be quoted as calibration | `calibrationEvidence: false`; CLI prints a warning; `judge_authority()` returns `advisory` |
| Pre-registered bars are enforced in code | `tests/test_intent_threshold_calibration.py` |
| Runtime threshold unchanged | same test pins `0.65` |
| Re-entry triggers documented | this section and the seven triggers above |
