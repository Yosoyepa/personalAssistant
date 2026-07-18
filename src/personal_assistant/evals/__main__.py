"""Command-line entry point for deterministic eval release gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from personal_assistant.evals.runner import SuiteValidationError, run_suite


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--tier", action="append", default=[])
    parser.add_argument("--failure-mode", action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run = run_suite(
            args.suite,
            categories=args.category,
            tiers=args.tier,
            failure_modes=args.failure_mode,
        )
    except SuiteValidationError as exc:
        if args.json_output:
            print(json.dumps({"schemaVersion": 1, "error": str(exc)}))
        else:
            print(f"INVALID SUITE: {exc}", file=sys.stderr)
        return 2
    except Exception:
        if args.json_output:
            print(json.dumps({"schemaVersion": 1, "error": "unexpected runner failure"}))
        else:
            print("INVALID SUITE: unexpected runner failure", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(run.as_dict(), ensure_ascii=False))
    else:
        for result in run.results:
            print(f"{'PASS' if result.passed else 'FAIL'} {result.id}")
            for error in result.errors:
                print(f"  {error}")
        print(
            f"Suite {run.suite_id}: {run.passed}/{run.selected} passed, "
            f"{run.failed} failed"
        )
    return 1 if run.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
