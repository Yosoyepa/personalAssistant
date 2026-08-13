from __future__ import annotations

import argparse
import json
import subprocess
import sys

from tools.wct.accept.pipeline import generate, ir_dry, parse_feature, run_mutations
from tools.wct.adopt import inspect_repository, render_inventory
from tools.wct.archmetrics.analyzer import analyze as analyze_architecture
from tools.wct.config import ConfigError, find_root
from tools.wct.doctor.checks import diagnose
from tools.wct.dry.analyzer import analyze as analyze_dry
from tools.wct.gate.runner import TIERS, run_tier
from tools.wct.hooks.guard import dispatch as dispatch_hook
from tools.wct.integrity.engine import bless, violations, write_lock
from tools.wct.introvert.analyzer import analyze as analyze_tests
from tools.wct.mutate.engine import run as run_mutation
from tools.wct.mutate.engine import scan as scan_mutation
from tools.wct.mutate.engine import update_manifest
from tools.wct.ratchet.measure import check as check_ratchets
from tools.wct.ratchet.measure import record as record_ratchets
from tools.wct.report.overview import overview
from tools.wct.report.render import json_report, text_report
from tools.wct.rules.engine import build as build_rules
from tools.wct.rules.engine import drift as rule_drift
from tools.wct.selftest.redteam import run as run_redteam
from tools.wct.webhook import send_from_environment


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="wct", description="Well Code hardening harness")
    root.add_argument("--version", action="version", version="wct 0.1.0")
    sub = root.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("gate", help="run a quality gate tier")
    gate.add_argument("--tier", choices=sorted(TIERS), default="fast")
    gate.add_argument("--json", action="store_true")
    gate.add_argument("--quiet", action="store_true")

    rules = sub.add_parser("rules", help="build or check provider rule copies")
    rules.add_argument("action", choices=["build", "check"])

    sub.add_parser("doctor", help="diagnose installation and hook wiring")

    integrity = sub.add_parser("integrity", help="protect gate configuration")
    integrity.add_argument("action", choices=["lock", "check", "bless"])
    integrity.add_argument("--reason")
    integrity.add_argument("--approved-by")

    hook = sub.add_parser("hook", help="execute a lifecycle guard")
    hook.add_argument("event")

    arch = sub.add_parser("archmetrics", help="calculate fan-in/out, I, A, D and zones")
    arch.add_argument("--json", action="store_true")

    dry = sub.add_parser("dry", help="find fuzzy structural duplication")
    dry.add_argument("paths", nargs="*")
    dry.add_argument("--json", action="store_true")

    introvert = sub.add_parser(
        "introvert", help="classify whether test assertions trace to the SUT"
    )
    introvert.add_argument("paths", nargs="*")
    introvert.add_argument("--json", action="store_true")

    mutate = sub.add_parser("mutate", help="differential mutation workflow")
    mutate.add_argument("action", choices=["scan", "run", "update-manifest"])

    accept = sub.add_parser("accept", help="Gherkin acceptance pipeline")
    accept.add_argument("action", choices=["parse", "ir-dry", "generate", "run", "mutate"])
    accept.add_argument("feature", nargs="?", default="features/example.feature")
    accept.add_argument("--output", default="tests/acceptance/generated/test_acceptance.py")
    accept.add_argument("--runner", nargs=argparse.REMAINDER)

    selftest = sub.add_parser("selftest", help="attack the harness with known-bad inputs")
    selftest.add_argument("suite", choices=["redteam"])

    sub.add_parser("report", help="show rule-to-gate coverage")

    ratchet = sub.add_parser("ratchet", help="check or record measured baselines")
    ratchet.add_argument("action", choices=["check", "record"])
    ratchet.add_argument("--approved-by")
    ratchet.add_argument("--reason")

    adopt = sub.add_parser("adopt", help="inventory an existing repository without modifying it")
    adopt.add_argument("target", nargs="?", default=".")

    webhook = sub.add_parser("webhook", help="send a signed, sanitized lifecycle event")
    webhook.add_argument("event")
    webhook.add_argument("--data", default="{}", help="small JSON object; secrets are forbidden")

    return root


def _json_object(raw: str) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("--data debe ser un objeto JSON")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = find_root()
        if args.command == "gate":
            results = run_tier(root, args.tier)
            print(json_report(results) if args.json else text_report(results, quiet=args.quiet))
            return 1 if any(result.blocking for result in results) else 0
        if args.command == "rules":
            if args.action == "build":
                for path in build_rules(root):
                    print(path.relative_to(root))
                return 0
            differences = rule_drift(root)
            for path in differences:
                print(f"DRIFT {path.relative_to(root)}", file=sys.stderr)
            return bool(differences)
        if args.command == "doctor":
            checks = diagnose(root)
            for ok, message in checks:
                print(f"{'PASS' if ok else 'FAIL'}  {message}")
            return 1 if any(not ok for ok, _ in checks) else 0
        if args.command == "integrity":
            if args.action == "lock":
                print(write_lock(root).relative_to(root))
                return 0
            if args.action == "check":
                problems = violations(root)
                print("\n".join(problems))
                return bool(problems)
            if not args.reason or not args.approved_by:
                print("bless requiere --reason y --approved-by", file=sys.stderr)
                return 2
            print(bless(root, args.reason, args.approved_by).relative_to(root))
            return 0
        if args.command == "hook":
            return dispatch_hook(args.event)
        if args.command == "archmetrics":
            report = analyze_architecture(root)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return bool(report["violations"])
        if args.command == "dry":
            paths = [root / path for path in args.paths] if args.paths else None
            report = analyze_dry(root, paths)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return bool(
                report["errors"]
                or any(item["ai_actionability"] == "EXTRACT" for item in report["candidates"])
            )
        if args.command == "introvert":
            paths = [root / path for path in args.paths] if args.paths else None
            report = analyze_tests(root, paths)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        if args.command == "mutate":
            if args.action == "scan":
                report = scan_mutation(root)
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return bool(report["over_limit"])
            if args.action == "update-manifest":
                print(update_manifest(root).relative_to(root))
                return 0
            return run_mutation(root)
        if args.command == "accept":
            feature = root / args.feature
            ir = parse_feature(feature)
            if args.action == "parse":
                print(json.dumps(ir, indent=2, ensure_ascii=False))
                return 0
            if args.action == "ir-dry":
                report = ir_dry(ir)
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return bool(report["count"])
            generated = generate(ir, root / args.output)
            if args.action == "generate":
                print(generated.relative_to(root))
                return 0
            if args.action == "run":
                return subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", str(generated.relative_to(root))],
                    cwd=root,
                    check=False,
                ).returncode
            command = args.runner or [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(generated.relative_to(root)),
            ]
            report = run_mutations(ir, command)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return bool(report["survived"])
        if args.command == "selftest":
            count, failures = run_redteam(root)
            for failure in failures:
                print(f"FAIL  {failure}")
            print(f"{count - len(failures)}/{count} adversarios rechazados")
            return bool(failures)
        if args.command == "report":
            print(json.dumps(overview(root), indent=2, ensure_ascii=False))
            return 0
        if args.command == "ratchet":
            if args.action == "check":
                failures = check_ratchets(root)
                print("\n".join(failures) if failures else "Todos los ratchets se mantienen.")
                return bool(failures)
            if not args.approved_by or not args.reason:
                print("record requiere --approved-by y --reason", file=sys.stderr)
                return 2
            for path in record_ratchets(root, args.approved_by, args.reason):
                print(path.relative_to(root))
            return 0
        if args.command == "adopt":
            print(render_inventory(inspect_repository((root / args.target).resolve())))
            return 0
        if args.command == "webhook":
            data = _json_object(args.data)
            print(send_from_environment(root, args.event, data))
            return 0
    except (ConfigError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"wct: {exc}", file=sys.stderr)
        return 2
    return 2
