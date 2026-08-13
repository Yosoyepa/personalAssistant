"""Build synthetic cassettes so the behavioral tier is runnable without a provider.

Phase 12 could not perform a live recording pass: this project has no LLM
provider configured (`LLM_PROVIDER=disabled`, no API key, empty egress
allowlist). Rather than leave the tier unrunnable, this script writes cassettes
marked `provenance: "synthetic"`, which `BehavioralRun.is_calibration_evidence`
refuses to treat as measured provider behavior.

What these cassettes are for: proving the harness is wired correctly end to end
— that prompts render, keys are stable, replay is deterministic, and the CLI
exits as documented. What they are NOT for: any TPR/TNR figure about a real
model. Those stay unpublished until someone runs `--mode record` against a
configured provider.

The fixture provider answers from each label's own `shouldAccept`, so a run over
these cassettes reproduces the labeler's intent rather than any model's
behavior. That circularity is the reason the provenance marker exists.

Recording goes through `run_corpus(mode="record")` rather than re-deriving the
requests here. The cassette key is a hash of the rendered prompt, so a
hand-built request that differed from the runner's by one character would
produce a cassette that misses on every replay. Letting the real runner issue
the calls makes the keys correct by construction.

Usage:
    uv run python scripts/build_synthetic_cassettes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from personal_assistant.application.dto.context import TokenBudget  # noqa: E402
from personal_assistant.application.dto.runtime import (  # noqa: E402
    LLMRequest,
    LLMResult,
)
from personal_assistant.evals.behavioral.corpus import load_corpus  # noqa: E402
from personal_assistant.evals.behavioral.judge import (  # noqa: E402
    JUDGE_SCHEMA_NAME,
)
from personal_assistant.evals.behavioral.runner import (  # noqa: E402
    EXTRACTION_SCHEMA_NAME,
    EXTRACTION_SURFACE,
    INTENT_SCHEMA_NAME,
    INTENT_SURFACE,
    run_corpus,
)

CORPUS_DIR = REPO_ROOT / "eval" / "behavioral"
RECORDED_AT = "1970-01-01T00:00:00+00:00"
"""Fixed: a real timestamp would churn the committed files on every rebuild."""

PROVIDER = "synthetic"
MODEL = "synthetic-fixture-v1"
ACCEPT_CONFIDENCE = 0.92
REJECT_CONFIDENCE = 0.30


class SyntheticProvider:
    """Answer prompts from the labels, keyed by the text embedded in the prompt.

    Dispatch is by `repr(text)`, which is exactly how both runtime prompt
    renderers substitute user text, and is scoped per surface: the same text may
    legitimately be labeled on both surfaces (`ic-106` and `re-034` are both
    `👍`), so a single flat map would be ambiguous. A prompt whose text matches
    no label means the runner rendered something this script does not model, so
    it raises rather than inventing a payload.
    """

    def __init__(self, by_surface: dict[str, dict[str, bool]]) -> None:
        self._by_surface = by_surface

    def _accepts(self, surface: str, prompt: str) -> bool:
        matches = [
            accept
            for text, accept in self._by_surface[surface].items()
            if repr(text) in prompt
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"{surface} prompt matched {len(matches)} labels; expected one"
            )
        return matches[0]

    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        if request.schema_name == JUDGE_SCHEMA_NAME:
            data: dict[str, object] = {
                "verdict": "pass",
                "confidence": ACCEPT_CONFIDENCE,
                "reason": "veredicto sintetico de fixture",
            }
        elif request.schema_name == INTENT_SCHEMA_NAME:
            accept = self._accepts(INTENT_SURFACE, request.prompt)
            data = {
                "kind": "reminder.create" if accept else "unsupported",
                "confidence": ACCEPT_CONFIDENCE if accept else REJECT_CONFIDENCE,
                "reminder_text": None,
            }
        elif request.schema_name == EXTRACTION_SCHEMA_NAME:
            accept = self._accepts(EXTRACTION_SURFACE, request.prompt)
            data = (
                {
                    "is_reminder": True,
                    "title": "tarea sintetica",
                    "starts_at": "2026-03-03T14:00:00+00:00",
                    "confidence": ACCEPT_CONFIDENCE,
                }
                if accept
                else {"is_reminder": False, "confidence": REJECT_CONFIDENCE}
            )
        else:
            raise RuntimeError(f"unexpected schema: {request.schema_name}")
        return LLMResult(
            provider=PROVIDER,
            model=MODEL,
            data=data,  # type: ignore[arg-type]
            input_tokens=0,
            output_tokens=0,
        )


def main() -> int:
    corpus = load_corpus(CORPUS_DIR)
    by_surface: dict[str, dict[str, bool]] = {
        INTENT_SURFACE: {},
        EXTRACTION_SURFACE: {},
    }
    for label in corpus.labels:
        by_surface[corpus.surface_of(label.id)][label.text] = label.shouldAccept

    run = run_corpus(
        CORPUS_DIR,
        mode="record",
        provider_factory=lambda: SyntheticProvider(by_surface),
        recorded_at=RECORDED_AT,
        provenance="synthetic",
    )
    print(f"recorded {run.completed}/{run.selected} labels, {run.errored} errored")
    for outcome in run.outcomes:
        if outcome.error is not None:
            print(f"  ERROR {outcome.label_id}: {outcome.error}")
    return 1 if run.errored else 0


if __name__ == "__main__":
    raise SystemExit(main())
