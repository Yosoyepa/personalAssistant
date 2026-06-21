# Eval Fixtures

The first eval layer is deterministic code assertions. These cases seed golden,
failure-mode, and regression tests for the local MVP.

Run:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests
```

LLM-as-judge is intentionally absent for v0 because tenancy, permission,
idempotency, schema validity, and prompt-injection blocking are code-checkable.
