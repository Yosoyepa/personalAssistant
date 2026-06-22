# Eval Fixtures

The first eval layer is deterministic code assertions. These cases seed golden,
failure-mode, and regression tests for the local MVP.

Run:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
PYTHONPATH=src python3 -B -m compileall src tests
python3 -m json.tool eval/cases.json >/dev/null
```

## Case Tiers

- `golden` - expected supported behavior for the local deterministic MVP.
- `failure-mode` - named contract failure modes and forbidden-action probes.
- `regression` - permanent cases created after observed defects.

Each case should map to `agents/personal_assistant/contract.md` via
`contractRefs`. Prefer code assertions for any behavior that can be checked
deterministically: tenancy, permissions, idempotency, schema validity, trace
shape, prompt-injection blocking, and tool allowlists.

LLM-as-judge is intentionally absent for v0 because the current high-risk
behaviors are code-checkable. Add a judge only when a subjective behavior cannot
be asserted with code, and keep judge output binary pass/fail.

## Merge Gate

- Every existing deterministic test passes.
- Every new failure mode gets a fixture in `eval/cases.json`.
- Every forbidden action in the contract has either a code test or an eval case.
- No accepted regression lowers the route-level pass rate below the ADR target.
- Any accepted regression documents the reason and owner in the case metadata.
