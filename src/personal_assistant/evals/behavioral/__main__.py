"""Command-line entry point for the Level-2 behavioral eval tier.

Mirrors `personal_assistant.evals.__main__`: exit 2 for an unusable corpus or an
unexpected failure, exit 1 when the run has errors, exit 0 otherwise.

What exit 1 means here is narrower than in L1, and deliberately so. This tier
fails on **harness** problems — a cassette that cannot answer a request, a
payload the runtime's own parser rejects, a judge that broke. It does not fail
on model disagreement with a human label. Disagreement is the measurement the
tier publishes; wiring it to the release gate would make CI depend on a provider
matching one person's judgement, which is exactly the coupling
`docs/adr/ADR-006-behavioral-eval-tier-and-judge.md` refuses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from personal_assistant.evals.behavioral.corpus import CorpusValidationError
from personal_assistant.evals.behavioral.runner import (
    BehavioralRun,
    BehavioralRunError,
    run_corpus,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the behavioral eval tier.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("replay", "record", "live"),
        default="replay",
        help="replay reads committed cassettes and needs no network.",
    )
    parser.add_argument("--surface", action="append", default=[])
    parser.add_argument("--split", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _live_provider_factory() -> object:
    """Build the configured provider only when a live mode actually needs one.

    Imported lazily: `replay` must not touch settings, secrets, or the egress
    allowlist, and a module-level import would make an unconfigured environment
    fail a run that never intended to reach the network.
    """
    from personal_assistant.infrastructure.bootstrap import build_llm_provider
    from personal_assistant.infrastructure.config import AppSettings

    provider = build_llm_provider(AppSettings())
    if provider is None:
        raise BehavioralRunError("no LLM provider is configured (LLM_PROVIDER)")
    return provider


def _print_human(run: BehavioralRun) -> None:
    for outcome in run.outcomes:
        if not outcome.completed:
            print(f"ERROR {outcome.label_id}")
            print(f"  {outcome.error}")
            continue
        marker = "AGREE" if outcome.agrees else "DIFFER"
        print(f"{marker} {outcome.label_id}")
    if not run.is_calibration_evidence:
        print(
            f"WARNING provenance={run.provenance}: these rates measure the harness, "
            "not a provider. Do not publish them as calibration."
        )
    for name, split, matrix in run.headline_matrices():
        tpr = matrix.true_positive_rate
        tnr = matrix.true_negative_rate
        print(
            f"{name}/{split}: TPR={tpr.value} (n={tpr.total}) "
            f"TNR={tnr.value} (n={tnr.total})"
        )
    decision = run.judge_authority()
    print(f"judge authority: {decision.authority}")
    for reason in decision.reasons:
        print(f"  - {reason}")
    print(
        f"Corpus {run.corpus_id} ({run.mode}): {run.completed}/{run.selected} "
        f"completed, {run.errored} errored"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run = run_corpus(
            args.corpus,
            mode=args.mode,
            surfaces=args.surface,
            splits=args.split,
            tags=args.tag,
            provider_factory=(
                None if args.mode == "replay" else _live_provider_factory  # type: ignore[arg-type]  # reason: en modo replay se pasa None a propósito; el runner no construye proveedor
            ),
        )
    except (CorpusValidationError, BehavioralRunError) as exc:
        if args.json_output:
            print(json.dumps({"schemaVersion": 1, "error": str(exc)}))
        else:
            print(f"INVALID CORPUS: {exc}", file=sys.stderr)
        return 2
    except Exception:
        if args.json_output:
            print(
                json.dumps({"schemaVersion": 1, "error": "unexpected runner failure"})
            )
        else:
            print("INVALID CORPUS: unexpected runner failure", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(run.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        _print_human(run)
    return 1 if run.errored else 0


if __name__ == "__main__":  # pragma: no cover  # reason: entrada CLI directa; no se ejecuta bajo pytest
    raise SystemExit(main())
