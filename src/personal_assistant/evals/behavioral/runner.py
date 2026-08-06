"""Run the labeled corpus against the runtime's two real LLM call sites.

The runner calls the *runtime's own* prompt renderers and result parsers rather
than re-implementing them. That is the whole point: an eval that renders its own
copy of the prompt keeps passing while the shipped prompt drifts away from it.
The private imports below are deliberate couplings, not conveniences.

`now` is pinned to `CORPUS_NOW`. Every prompt embeds it, and the cassette key is
a hash of the prompt, so a moving clock would invalidate every recorded response
on every run and replay could never be deterministic. Changing `CORPUS_NOW`
means re-recording the cassettes.

What each surface measures:

* **intent-classification** reproduces `CommandService._infer_intent`, including
  `accepted = confidence >= LLM_INTENT_CONFIDENCE_THRESHOLD`. The confusion
  matrix against `shouldAccept` is what turns that hand-picked constant into a
  measured one.
* **reminder-extraction** reproduces `_extract_with_llm`, then asks the judge to
  grade the result. The judge's ground truth is derived: the human said whether
  the runtime *should* accept, so agreement between that and what the runtime
  actually did is whether the outcome was acceptable. This measures the judge on
  the accept/decline decision only, not on field-level fidelity — the labels
  predate the extractions, so no human ever graded a title or a timestamp.

Harness failures and model disagreement are kept strictly apart. Only a harness
failure (missing cassette entry, malformed payload, judge that broke) counts as
an error and fails the run. Disagreement with a human label is the measurement,
not a defect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from personal_assistant.application.dto.commands import InferredCommandIntent
from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.reminders import ReminderWorkflowInput
from personal_assistant.application.dto.runtime import LLMRequest
from personal_assistant.application.ports.prompts import PromptCatalogPort
from personal_assistant.application.ports.services import LLMProvider
from personal_assistant.application.services.prompts import DefaultPromptCatalog
from personal_assistant.application.use_cases.commands import (
    LLM_INTENT_CONFIDENCE_THRESHOLD,
    _render_intent_prompt,
)
from personal_assistant.application.use_cases.reminders import (
    _reminder_extraction_from_llm,
    _render_reminder_extraction_prompt,
)
from personal_assistant.domain.reminders.models import ReminderExtraction
from personal_assistant.evals.behavioral.calibration import (
    AuthorityDecision,
    judge_authority,
)
from personal_assistant.evals.behavioral.corpus import (
    LabeledCorpus,
    load_corpus,
    select,
)
from personal_assistant.evals.behavioral.judge import judge_extraction
from personal_assistant.evals.behavioral.metrics import (
    ConfusionMatrix,
    Rate,
    confusion_matrix,
    threshold_sweep,
    wilson_interval,
)
from personal_assistant.evals.behavioral.replay import (
    CassetteError,
    RecordingLLMProvider,
    ReplayLLMProvider,
    read_cassette,
    write_cassette,
)
from personal_assistant.evals.behavioral.schema import IntentLabel, Provenance

Mode = Literal["replay", "record", "live"]

CORPUS_NOW = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
"""Pinned reference instant: Monday 2026-03-02, 09:00 in `CORPUS_TIMEZONE`."""

CORPUS_TIMEZONE = "America/Bogota"
INTENT_SURFACE = "intent-classification"
EXTRACTION_SURFACE = "reminder-extraction"
CASSETTE_DIRNAME = "cassettes"

INTENT_SCHEMA_NAME = "conversation_intent"
INTENT_MAX_TOKENS = 256
INTENT_TOKEN_LIMIT = 1_000
EXTRACTION_SCHEMA_NAME = "reminder_extraction"
EXTRACTION_MAX_TOKENS = 384
EXTRACTION_TOKEN_LIMIT = 1_500
LLM_TEMPERATURE = 0.0

SWEEP_THRESHOLDS = [round(0.05 * step, 2) for step in range(1, 20)]
"""0.05 through 0.95. Includes 0.65, so one point reproduces shipped behavior."""


class BehavioralRunError(ValueError):
    """The run cannot start because its inputs or mode are unusable."""


def _sanitize(exc: Exception) -> str:
    """Name a failure without carrying provider-supplied text.

    Errors this package raises itself are quoted in full: their messages are
    built from schema names and key prefixes, which hold no user text. Anything
    else contributes its class name only, for the reason `judge._sanitize`
    documents — a provider that echoes the request would otherwise copy corpus
    text into a committed report.
    """
    if isinstance(exc, CassetteError | BehavioralRunError):
        return f"{exc.__class__.__name__}: {exc}"
    return exc.__class__.__name__


@dataclass(frozen=True, slots=True)
class LabelOutcome:
    """What the runtime did with one label, and whether that matched the human.

    ``error`` is the only field that fails a run. ``agrees`` being ``False`` is a
    measurement: it means the model and the labeler disagreed, which is the
    quantity the whole tier exists to report.
    """

    label_id: str
    surface: str
    split: str
    expected_accept: bool
    expected_kind: str
    actual_accept: bool | None = None
    actual_kind: str | None = None
    confidence: float | None = None
    judge_accept: bool | None = None
    judge_expected: bool | None = None
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.error is None

    @property
    def agrees(self) -> bool | None:
        if self.actual_accept is None:
            return None
        return self.actual_accept == self.expected_accept

    @property
    def kind_agrees(self) -> bool | None:
        if self.actual_kind is None:
            return None
        return self.actual_kind == self.expected_kind

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.label_id,
            "surface": self.surface,
            "split": self.split,
            "expected": {"accept": self.expected_accept, "kind": self.expected_kind},
            "actual": {
                "accept": self.actual_accept,
                "kind": self.actual_kind,
                "confidence": (
                    None if self.confidence is None else round(self.confidence, 4)
                ),
            },
            "judge": {"accept": self.judge_accept, "expected": self.judge_expected},
            "agrees": self.agrees,
            "error": self.error,
        }


def _errored(label: IntentLabel, surface: str, exc: Exception) -> LabelOutcome:
    return LabelOutcome(
        label_id=label.id,
        surface=surface,
        split=label.split,
        expected_accept=label.shouldAccept,
        expected_kind=label.expectedKind,
        error=_sanitize(exc),
    )


def intent_request(
    *,
    text: str,
    now: datetime,
    timezone: str,
    prompt_catalog: PromptCatalogPort,
) -> LLMRequest:
    """Build the exact request `CommandService._infer_intent` sends."""
    rendered = _render_intent_prompt(
        text=text,
        now=now,
        timezone=timezone,
        prompt_catalog=prompt_catalog,
    )
    return LLMRequest(
        schema_name=INTENT_SCHEMA_NAME,
        max_tokens=INTENT_MAX_TOKENS,
        temperature=LLM_TEMPERATURE,
        prompt=rendered.text,
    )


def extraction_request(
    *,
    text: str,
    now: datetime,
    timezone: str,
    prompt_catalog: PromptCatalogPort,
) -> LLMRequest:
    """Build the exact request `_extract_with_llm` sends."""
    workflow_input = ReminderWorkflowInput(
        message_id="behavioral",
        source_event_id="behavioral",
        conversation_id="behavioral",
        recipient="behavioral",
        text=text,
        now=now,
        timezone=timezone,
    )
    rendered = _render_reminder_extraction_prompt(
        workflow_input, prompt_catalog=prompt_catalog
    )
    return LLMRequest(
        schema_name=EXTRACTION_SCHEMA_NAME,
        max_tokens=EXTRACTION_MAX_TOKENS,
        temperature=LLM_TEMPERATURE,
        prompt=rendered.text,
    )


def _extraction_payload(
    extraction: ReminderExtraction | None,
) -> dict[str, object] | None:
    if extraction is None:
        return None
    return dict(extraction.model_dump(mode="json"))


def _run_intent_label(
    label: IntentLabel,
    *,
    llm: LLMProvider,
    prompt_catalog: PromptCatalogPort,
    now: datetime,
    timezone: str,
) -> LabelOutcome:
    try:
        request = intent_request(
            text=label.text,
            now=now,
            timezone=timezone,
            prompt_catalog=prompt_catalog,
        )
        result = llm.complete(request, budget=TokenBudget(limit=INTENT_TOKEN_LIMIT))
        inferred = InferredCommandIntent.model_validate(result.data)
    except Exception as exc:
        return _errored(label, INTENT_SURFACE, exc)
    return LabelOutcome(
        label_id=label.id,
        surface=INTENT_SURFACE,
        split=label.split,
        expected_accept=label.shouldAccept,
        expected_kind=label.expectedKind,
        actual_accept=inferred.confidence >= LLM_INTENT_CONFIDENCE_THRESHOLD,
        actual_kind=inferred.kind.value,
        confidence=inferred.confidence,
    )


def _run_extraction_label(
    label: IntentLabel,
    *,
    llm: LLMProvider,
    prompt_catalog: PromptCatalogPort,
    now: datetime,
    timezone: str,
) -> LabelOutcome:
    try:
        request = extraction_request(
            text=label.text,
            now=now,
            timezone=timezone,
            prompt_catalog=prompt_catalog,
        )
        result = llm.complete(request, budget=TokenBudget(limit=EXTRACTION_TOKEN_LIMIT))
        extraction = _reminder_extraction_from_llm(dict(result.data), timezone=timezone)
    except Exception as exc:
        return _errored(label, EXTRACTION_SURFACE, exc)

    accepted = extraction is not None
    verdict = judge_extraction(
        label_id=label.id,
        text=label.text,
        extraction=_extraction_payload(extraction),
        now=now,
        timezone=timezone,
        llm=llm,
        prompt_catalog=prompt_catalog,
    )
    if not verdict.usable:
        # A judge that broke is a harness failure, not a FAIL verdict. Scoring it
        # as a genuine rejection would flatter the true negative rate.
        return LabelOutcome(
            label_id=label.id,
            surface=EXTRACTION_SURFACE,
            split=label.split,
            expected_accept=label.shouldAccept,
            expected_kind=label.expectedKind,
            actual_accept=accepted,
            error=f"judge unusable ({verdict.error})",
        )
    return LabelOutcome(
        label_id=label.id,
        surface=EXTRACTION_SURFACE,
        split=label.split,
        expected_accept=label.shouldAccept,
        expected_kind=label.expectedKind,
        actual_accept=accepted,
        actual_kind=label.expectedKind if accepted else None,
        confidence=None if extraction is None else extraction.confidence,
        judge_accept=verdict.accepted,
        judge_expected=accepted == label.shouldAccept,
    )


def _matrix_for(outcomes: Sequence[LabelOutcome]) -> ConfusionMatrix:
    return confusion_matrix(
        [
            (outcome.expected_accept, bool(outcome.actual_accept))
            for outcome in outcomes
            if outcome.completed and outcome.actual_accept is not None
        ]
    )


def _judge_matrix_for(outcomes: Sequence[LabelOutcome]) -> ConfusionMatrix:
    return confusion_matrix(
        [
            (bool(outcome.judge_expected), bool(outcome.judge_accept))
            for outcome in outcomes
            if outcome.completed and outcome.judge_accept is not None
        ]
    )


def _kind_agreement(outcomes: Sequence[LabelOutcome]) -> Rate:
    graded = [
        outcome
        for outcome in outcomes
        if outcome.completed and outcome.kind_agrees is not None
    ]
    hits = sum(1 for outcome in graded if outcome.kind_agrees)
    return Rate(hits, len(graded), wilson_interval(hits, len(graded)))


def _by_split(
    outcomes: Sequence[LabelOutcome],
    build: Callable[[Sequence[LabelOutcome]], ConfusionMatrix],
) -> dict[str, object]:
    """Report each split separately.

    Splits are never pooled into a headline number: a threshold chosen on
    calibration and then scored on the pooled set would be reporting partly on
    the data that picked it.
    """
    return {
        split: build(
            [outcome for outcome in outcomes if outcome.split == split]
        ).as_dict()
        for split in ("calibration", "holdout")
    }


@dataclass(frozen=True, slots=True)
class BehavioralRun:
    corpus_id: str
    mode: str
    outcomes: tuple[LabelOutcome, ...]
    provenance: str = "recorded"
    """Weakest provenance among the cassettes this run replayed.

    ``synthetic`` propagates from any single surface, because a report is only as
    trustworthy as its least trustworthy input.
    """

    @property
    def is_calibration_evidence(self) -> bool:
        """Whether these rates may be cited as measured provider behavior.

        A synthetic replay exercises the harness and nothing else. Returning
        ``False`` here is the code-level refusal that keeps a hand-authored
        fixture from being quoted as a calibration result.
        """
        return self.provenance == "recorded"

    @property
    def selected(self) -> int:
        return len(self.outcomes)

    @property
    def errored(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.completed)

    @property
    def completed(self) -> int:
        return self.selected - self.errored

    def surface(self, name: str) -> tuple[LabelOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.surface == name)

    def headline_matrices(self) -> tuple[tuple[str, str, ConfusionMatrix], ...]:
        """`(label, split, matrix)` triples for human-readable output."""
        blocks: list[
            tuple[str, Sequence[LabelOutcome], Callable[..., ConfusionMatrix]]
        ] = [
            ("intentClassification", self.surface(INTENT_SURFACE), _matrix_for),
            ("reminderExtraction", self.surface(EXTRACTION_SURFACE), _matrix_for),
            ("judge", self.surface(EXTRACTION_SURFACE), _judge_matrix_for),
        ]
        return tuple(
            (name, split, build([o for o in outcomes if o.split == split]))
            for name, outcomes, build in blocks
            if outcomes
            for split in ("calibration", "holdout")
        )

    def judge_authority(self) -> AuthorityDecision:
        """Whether this run's evidence lets the judge block a build.

        Computed from the holdout split only, and reported even when the answer
        is the boring one, so the report always states the judge's standing
        instead of leaving a reader to infer it from the rates.
        """
        extraction = [
            outcome
            for outcome in self.surface(EXTRACTION_SURFACE)
            if outcome.split == "holdout"
        ]
        matrix = _judge_matrix_for(extraction) if extraction else None
        return judge_authority(
            matrix,
            split="holdout",
            is_calibration_evidence=self.is_calibration_evidence,
        )

    def metrics(self) -> dict[str, object]:
        intent = self.surface(INTENT_SURFACE)
        extraction = self.surface(EXTRACTION_SURFACE)
        report: dict[str, object] = {}
        if intent:
            scored = [
                (outcome.expected_accept, outcome.confidence or 0.0)
                for outcome in intent
                if outcome.completed and outcome.confidence is not None
            ]
            report["intentClassification"] = {
                "threshold": LLM_INTENT_CONFIDENCE_THRESHOLD,
                "bySplit": _by_split(intent, _matrix_for),
                "kindAgreement": _kind_agreement(intent).as_dict(),
                "thresholdSweep": [
                    point.as_dict()
                    for point in threshold_sweep(scored, SWEEP_THRESHOLDS)
                ]
                if scored
                else [],
            }
        if extraction:
            report["judge"] = {
                "bySplit": _by_split(extraction, _judge_matrix_for),
                "authority": self.judge_authority().as_dict(),
            }
            report["reminderExtraction"] = {
                "bySplit": _by_split(extraction, _matrix_for),
            }
        return report

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "corpusId": self.corpus_id,
            "mode": self.mode,
            "provenance": self.provenance,
            "calibrationEvidence": self.is_calibration_evidence,
            "summary": {
                "selected": self.selected,
                "completed": self.completed,
                "errored": self.errored,
            },
            "metrics": self.metrics(),
            "results": [outcome.as_dict() for outcome in self.outcomes],
        }


def cassette_path(corpus_dir: Path, surface: str) -> Path:
    return corpus_dir / CASSETTE_DIRNAME / f"{surface}.json"


_RUNNERS: dict[str, Callable[..., LabelOutcome]] = {
    INTENT_SURFACE: _run_intent_label,
    EXTRACTION_SURFACE: _run_extraction_label,
}


def _replay_provider(corpus_dir: Path, surface: str) -> tuple[ReplayLLMProvider, str]:
    path = cassette_path(corpus_dir, surface)
    if not path.is_file():
        raise BehavioralRunError(
            f"no cassette for surface {surface!r} at {path.name}; "
            "record it with --mode record"
        )
    cassette = read_cassette(path)
    entries = {entry.key: entry for entry in cassette.entries}
    return ReplayLLMProvider(entries), cassette.provenance


def run_corpus(
    corpus_dir: Path,
    *,
    mode: Mode = "replay",
    surfaces: Iterable[str] = (),
    splits: Iterable[str] = (),
    tags: Iterable[str] = (),
    now: datetime = CORPUS_NOW,
    timezone: str = CORPUS_TIMEZONE,
    prompt_catalog: PromptCatalogPort | None = None,
    provider_factory: Callable[[], LLMProvider] | None = None,
    recorded_at: str | None = None,
    provenance: Provenance = "recorded",
) -> BehavioralRun:
    """Execute the selected labels and return a scored run.

    `provider_factory` is injected rather than built here so a test can exercise
    every mode without a network stack, and so `replay` never constructs a live
    provider even by accident.

    `provenance` stamps what a `record` run is writing. It defaults to
    `recorded`, so marking a cassette `synthetic` is always a deliberate act by
    the caller that knows its provider is a fixture.
    """
    if mode not in ("replay", "record", "live"):
        raise BehavioralRunError(f"unknown mode: {mode}")
    if mode in ("record", "live") and provider_factory is None:
        raise BehavioralRunError(f"mode {mode} requires a live provider")

    corpus: LabeledCorpus = load_corpus(corpus_dir)
    selected = select(corpus, splits=splits, tags=tags, surfaces=surfaces)
    catalog = prompt_catalog or DefaultPromptCatalog()

    outcomes: list[LabelOutcome] = []
    provenances: set[str] = set()
    for surface in sorted({corpus.surface_of(label.id) for label in selected}):
        runner = _RUNNERS.get(surface)
        if runner is None:
            raise BehavioralRunError(f"no runner for surface: {surface}")

        recorder: RecordingLLMProvider | None = None
        llm: LLMProvider
        if mode == "replay":
            replayer, cassette_provenance = _replay_provider(corpus_dir, surface)
            llm = replayer
            provenances.add(cassette_provenance)
        elif mode == "record":
            assert provider_factory is not None
            recorder = RecordingLLMProvider(provider_factory())
            llm = recorder
            provenances.add(provenance)
        else:
            assert provider_factory is not None
            llm = provider_factory()
            provenances.add("recorded")

        for label in selected:
            if corpus.surface_of(label.id) != surface:
                continue
            outcomes.append(
                runner(
                    label,
                    llm=llm,
                    prompt_catalog=catalog,
                    now=now,
                    timezone=timezone,
                )
            )

        if recorder is not None:
            stamp = recorded_at or datetime.now(UTC).isoformat()
            path = cassette_path(corpus_dir, surface)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_cassette(
                path,
                recorder.as_cassette(recorded_at=stamp, provenance=provenance),
            )

    return BehavioralRun(
        corpus_id=corpus.corpus_id,
        mode=mode,
        outcomes=tuple(sorted(outcomes, key=lambda outcome: outcome.label_id)),
        provenance=("synthetic" if "synthetic" in provenances else "recorded"),
    )
